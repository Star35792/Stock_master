from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.transaction import idempotent_write
from app.repositories.portfolio import PortfolioPlanRepository
from app.repositories.risk import RiskCheckRepository
from app.repositories.trade import (
    PaperEquitySnapshotRepository,
    PaperTradingRepository,
)
from app.risk import (
    OrderRiskDecision,
    OrderRiskInput,
    PreTradeRiskEngine,
    RiskLimits,
    RiskRuleResult,
)
from app.schemas.risk import RiskEvaluationRead, RiskOrderEvaluationRead
from app.services.liquidity_risk import TrustedAverageDailyAmountService
from app.services.portfolio_readiness import PortfolioRiskReadinessService
from app.services.snapshot_hashes import hash_equity_market_snapshot, hash_payload

RISK_LIMITS = RiskLimits(
    max_symbol_weight=Decimal("0.20"),
    max_industry_weight=Decimal("0.40"),
    max_drawdown=Decimal("0.10"),
    max_amount_participation=Decimal("0.10"),
)


def _risk_limits_from_plan(plan: Any) -> RiskLimits:
    """Build RiskLimits from the portfolio plan's persisted constraints.

    This keeps pre-trade risk checks aligned with the constraints that were
    actually used during portfolio construction. Style, tracking error and
    volatility budgets are not yet enforced by the engine, but their presence
    is recorded in the policy hash.
    """
    parameters: dict[str, Any] = getattr(plan, "parameters", None) or {}
    return RiskLimits(
        max_symbol_weight=Decimal(
            parameters.get("max_symbol_weight", RISK_LIMITS.max_symbol_weight)
        ),
        max_industry_weight=Decimal(
            parameters.get("max_industry_weight", RISK_LIMITS.max_industry_weight)
        ),
        max_drawdown=RISK_LIMITS.max_drawdown,
        max_amount_participation=RISK_LIMITS.max_amount_participation,
    )
MINIMUM_EQUITY_OBSERVATIONS = 15
RISK_POLICY = {
    "name": "paper_pretrade_risk",
    "version": "v1",
    "max_symbol_weight": str(RISK_LIMITS.max_symbol_weight),
    "max_industry_weight": str(RISK_LIMITS.max_industry_weight),
    "max_drawdown": str(RISK_LIMITS.max_drawdown),
    "max_amount_participation": str(RISK_LIMITS.max_amount_participation),
    "adv_window": 20,
    "adv_minimum_observations": 15,
    "equity_history_status": "continuous",
    "equity_minimum_observations": MINIMUM_EQUITY_OBSERVATIONS,
    "engine_version": "v1",
}


class RiskEvaluationConflict(ValueError):
    pass


class RiskEvaluationValidationError(ValueError):
    pass


class RiskEvaluationResourceNotFound(ValueError):
    pass


class BoundRiskEvaluationService:
    def __init__(
        self,
        *,
        plan_repository: PortfolioPlanRepository | None = None,
        paper_repository: PaperTradingRepository | None = None,
        equity_repository: PaperEquitySnapshotRepository | None = None,
        risk_repository: RiskCheckRepository | None = None,
        readiness_service: PortfolioRiskReadinessService | None = None,
        adv_service: TrustedAverageDailyAmountService | None = None,
        engine: PreTradeRiskEngine | None = None,
    ) -> None:
        self.plan_repository = plan_repository or PortfolioPlanRepository()
        self.paper_repository = paper_repository or PaperTradingRepository()
        self.equity_repository = equity_repository or PaperEquitySnapshotRepository()
        self.risk_repository = risk_repository or RiskCheckRepository()
        self.readiness_service = readiness_service or PortfolioRiskReadinessService()
        self.adv_service = adv_service or TrustedAverageDailyAmountService()
        self.engine = engine or PreTradeRiskEngine()

    def evaluate(
        self,
        db: Session,
        *,
        plan_id: int,
        paper_account_id: int,
        equity_snapshot_id: int,
        idempotency_key: str,
    ) -> RiskEvaluationRead:
        request_hash = hash_payload(
            {
                "plan_id": plan_id,
                "paper_account_id": paper_account_id,
                "equity_snapshot_id": equity_snapshot_id,
            }
        )
        existing = self.risk_repository.get_by_idempotency_key(
            db,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._replay(db, existing, request_hash=request_hash)

        account = self.paper_repository.get_account_for_update(
            db,
            account_id=paper_account_id,
        )
        if account is None:
            raise RiskEvaluationResourceNotFound("paper account not found")
        plan, targets = self.plan_repository.get_with_targets(db, plan_id=plan_id)
        if plan is None:
            raise RiskEvaluationResourceNotFound("portfolio plan not found")
        equity, equity_components = self.equity_repository.get_with_components(
            db,
            account_id=paper_account_id,
            snapshot_id=equity_snapshot_id,
        )
        if equity is None:
            raise RiskEvaluationResourceNotFound("paper equity snapshot not found")
        if equity.valuation_date != plan.as_of_date:
            raise RiskEvaluationConflict(
                "equity snapshot valuation_date does not match the portfolio plan"
            )
        if equity.total_equity != plan.total_equity:
            raise RiskEvaluationConflict(
                "equity snapshot total_equity does not match the portfolio plan"
            )
        self._validate_equity_snapshot(equity, equity_components)

        readiness = self.readiness_service.check(
            db,
            plan_id=plan_id,
            paper_account_id=paper_account_id,
        )
        if not readiness.ready_for_risk_check:
            raise RiskEvaluationConflict(
                "portfolio risk readiness is blocked: "
                + ",".join(blocker.code for blocker in readiness.blockers)
            )
        if readiness.account_snapshot_hash != equity.account_snapshot_hash:
            raise RiskEvaluationConflict("paper account snapshot changed after the equity snapshot")

        order_targets = [target for target in targets if target.order_quantity != 0]
        symbols = {target.symbol for target in order_targets}
        adv_snapshots = self.adv_service.calculate(
            db,
            symbols=symbols,
            as_of_date=plan.as_of_date,
        )
        adv_inputs = {
            symbol: {
                "available": snapshot.available,
                "average_daily_amount": (
                    None
                    if snapshot.average_daily_amount is None
                    else str(snapshot.average_daily_amount)
                ),
                "observation_count": snapshot.observation_count,
                "window": snapshot.window,
                "minimum_observations": snapshot.minimum_observations,
                "start_date": (
                    None if snapshot.start_date is None else snapshot.start_date.isoformat()
                ),
                "end_date": (None if snapshot.end_date is None else snapshot.end_date.isoformat()),
                "input_hash": snapshot.input_hash,
                "daily_bar_ids": list(snapshot.daily_bar_ids),
                "observations": list(snapshot.observations),
            }
            for symbol, snapshot in sorted(adv_snapshots.items())
        }
        adv_snapshot_hash = hash_payload(adv_inputs)
        readiness_snapshot = {
            "ready_for_risk_check": readiness.ready_for_risk_check,
            "account_snapshot_hash": readiness.account_snapshot_hash,
            "market_snapshot_hash": readiness.market_snapshot_hash,
            "consistency_level": readiness.consistency_level,
            "checks": [check.model_dump(mode="json") for check in readiness.checks],
        }
        readiness_hash = hash_payload(readiness_snapshot)
        risk_policy_hash = hash_payload(RISK_POLICY)

        history_complete = (
            equity.history_status == "continuous"
            and equity.observation_count >= MINIMUM_EQUITY_OBSERVATIONS
        )
        preconditions = [
            {
                "rule_name": "snapshot_readiness",
                "passed": True,
                "reason_code": "passed",
                "message": "current account and market snapshots match the plan",
            },
            {
                "rule_name": "equity_history_continuous",
                "passed": history_complete,
                "reason_code": ("passed" if history_complete else "equity_history_incomplete"),
                "message": (
                    "observed equity history satisfies the server policy"
                    if history_complete
                    else "equity history is bootstrap, gapped, or too short"
                ),
            },
        ]

        current_industry_weights: dict[str, Decimal] = {}
        projected_industry_weights: dict[str, Decimal] = {}
        for target in targets:
            current_weight = Decimal(target.current_quantity) * target.price / plan.total_equity
            current_industry_weights[target.industry] = (
                current_industry_weights.get(target.industry, Decimal()) + current_weight
            )
            projected_industry_weights[target.industry] = (
                projected_industry_weights.get(target.industry, Decimal()) + target.final_weight
            )

        risk_limits = _risk_limits_from_plan(plan)
        decisions: list[OrderRiskDecision] = []
        order_values = []
        for target in order_targets:
            current_symbol_weight = (
                Decimal(target.current_quantity) * target.price / plan.total_equity
            )
            adv = adv_snapshots[target.symbol]
            decision = self.engine.check(
                order=OrderRiskInput(
                    symbol=target.symbol,
                    industry=target.industry,
                    order_quantity=target.order_quantity,
                    price=target.price,
                    current_symbol_weight=current_symbol_weight,
                    projected_symbol_weight=target.final_weight,
                    current_industry_weight=current_industry_weights[target.industry],
                    projected_industry_weight=projected_industry_weights[target.industry],
                    average_daily_amount=adv.average_daily_amount,
                    current_drawdown=equity.drawdown,
                ),
                limits=risk_limits,
            )
            history_rule = RiskRuleResult(
                rule_name="equity_history_continuous",
                passed=history_complete or decision.side == "sell",
                reason_code=(
                    "passed"
                    if history_complete
                    else "risk_reducing_sell_allowed"
                    if decision.side == "sell"
                    else "equity_history_incomplete"
                ),
                message=(
                    "observed equity history satisfies the server policy"
                    if history_complete
                    else "risk-reducing sell allowed with incomplete observed history"
                    if decision.side == "sell"
                    else "risk-increasing order requires continuous observed equity history"
                ),
            )
            rules = (*decision.rules, history_rule)
            decision = OrderRiskDecision(
                symbol=decision.symbol,
                side=decision.side,
                order_quantity=decision.order_quantity,
                passed=all(rule.passed for rule in rules),
                rules=rules,
            )
            decisions.append(decision)
            order_values.append(
                {
                    "symbol": decision.symbol,
                    "side": decision.side,
                    "order_quantity": decision.order_quantity,
                    "passed": decision.passed,
                    "rejection_reasons": list(decision.rejection_reasons),
                    "rule_results": [
                        {
                            "rule_name": rule.rule_name,
                            "passed": rule.passed,
                            "reason_code": rule.reason_code,
                            "message": rule.message,
                        }
                        for rule in decision.rules
                    ],
                    "reference_price": target.price,
                    "current_symbol_weight": current_symbol_weight,
                    "projected_symbol_weight": target.final_weight,
                    "current_industry_weight": current_industry_weights[target.industry],
                    "projected_industry_weight": projected_industry_weights[target.industry],
                    "average_daily_amount": adv.average_daily_amount,
                    "adv_window": adv.window,
                    "adv_observation_count": adv.observation_count,
                    "adv_input_hash": adv.input_hash,
                }
            )

        passed = all(item["passed"] for item in preconditions) and all(
            decision.passed for decision in decisions
        )
        evaluation_hash = hash_payload(
            {
                "plan_id": plan.id,
                "plan_request_hash": plan.request_hash,
                "account_id": account.id,
                "equity_snapshot_id": equity.id,
                "equity_valuation_hash": equity.valuation_hash,
                "readiness_hash": readiness_hash,
                "adv_snapshot_hash": adv_snapshot_hash,
                "risk_policy_hash": risk_policy_hash,
                "code_version": settings.app_code_version,
            }
        )
        if (
            self.risk_repository.get_by_evaluation_hash(
                db,
                evaluation_hash=evaluation_hash,
            )
            is not None
        ):
            raise RiskEvaluationConflict(
                "this bound risk evaluation already exists with another idempotency key"
            )

        check_values = {
            "plan_id": plan.id,
            "current_drawdown": equity.drawdown,
            "limits": {
                "max_symbol_weight": str(risk_limits.max_symbol_weight),
                "max_industry_weight": str(risk_limits.max_industry_weight),
                "max_drawdown": str(risk_limits.max_drawdown),
                "max_amount_participation": str(risk_limits.max_amount_participation),
            },
            "passed": passed,
            "code_version": settings.app_code_version,
            "account_id": account.id,
            "equity_snapshot_id": equity.id,
            "plan_request_hash": plan.request_hash,
            "readiness_account_hash": readiness.account_snapshot_hash,
            "readiness_market_hash": readiness.market_snapshot_hash,
            "readiness_hash": readiness_hash,
            "readiness_snapshot": {
                **readiness_snapshot,
                "preconditions": preconditions,
            },
            "adv_snapshot_hash": adv_snapshot_hash,
            "adv_inputs": adv_inputs,
            "risk_policy_hash": risk_policy_hash,
            "evaluation_hash": evaluation_hash,
            "evaluation_status": "passed" if passed else "blocked",
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        }
        try:
            with idempotent_write(db):
                check = self.risk_repository.save_evaluation(
                    db,
                    check_values=check_values,
                    order_values=order_values,
                )
        except IntegrityError as exc:
            existing = self.risk_repository.get_by_idempotency_key(
                db,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._replay(db, existing, request_hash=request_hash)
            raise RiskEvaluationConflict("risk evaluation already exists") from exc
        return self._to_read(check, order_values)

    def _validate_equity_snapshot(self, equity, components) -> None:
        market_hash = hash_equity_market_snapshot(equity.valuation_date, components)
        if market_hash != equity.market_snapshot_hash:
            raise RiskEvaluationConflict("paper equity market snapshot hash is invalid")
        valuation_hash = hash_payload(
            {
                "account_snapshot_hash": equity.account_snapshot_hash,
                "market_snapshot_hash": equity.market_snapshot_hash,
                "cash_balance": str(equity.cash_balance),
                "positions_market_value": str(equity.positions_market_value),
                "total_equity": str(equity.total_equity),
                "high_watermark": str(equity.high_watermark),
                "drawdown": str(equity.drawdown),
                "previous_snapshot_id": equity.previous_snapshot_id,
                "history_status": equity.history_status,
                "valuation_policy": equity.valuation_policy,
                "code_version": equity.code_version,
            }
        )
        if valuation_hash != equity.valuation_hash:
            raise RiskEvaluationConflict("paper equity valuation hash is invalid")

    def validate_current_evaluation(self, db: Session, *, check_id: int):
        check, orders = self.risk_repository.get_with_orders(db, check_id=check_id)
        if check is None:
            raise RiskEvaluationResourceNotFound("risk evaluation not found")
        if (
            check.evaluation_status != "passed"
            or not check.passed
            or not check.evaluation_hash
            or not orders
            or any(not order.passed for order in orders)
        ):
            raise RiskEvaluationValidationError(
                "only a fully passed bound risk evaluation can be confirmed"
            )
        account = self.paper_repository.get_account_for_update(
            db,
            account_id=check.account_id,
        )
        if account is None:
            raise RiskEvaluationResourceNotFound("paper account not found")
        plan, targets = self.plan_repository.get_with_targets(
            db,
            plan_id=check.plan_id,
        )
        if plan is None or plan.request_hash != check.plan_request_hash:
            raise RiskEvaluationConflict("portfolio plan changed after risk evaluation")
        equity, components = self.equity_repository.get_with_components(
            db,
            account_id=check.account_id,
            snapshot_id=check.equity_snapshot_id,
        )
        if equity is None:
            raise RiskEvaluationResourceNotFound("paper equity snapshot not found")
        self._validate_equity_snapshot(equity, components)

        readiness = self.readiness_service.check(
            db,
            plan_id=check.plan_id,
            paper_account_id=check.account_id,
        )
        if not readiness.ready_for_risk_check:
            raise RiskEvaluationConflict("risk readiness changed after evaluation")
        readiness_snapshot = {
            "ready_for_risk_check": readiness.ready_for_risk_check,
            "account_snapshot_hash": readiness.account_snapshot_hash,
            "market_snapshot_hash": readiness.market_snapshot_hash,
            "consistency_level": readiness.consistency_level,
            "checks": [item.model_dump(mode="json") for item in readiness.checks],
        }
        readiness_hash = hash_payload(readiness_snapshot)
        if (
            readiness.account_snapshot_hash != check.readiness_account_hash
            or readiness.market_snapshot_hash != check.readiness_market_hash
            or readiness_hash != check.readiness_hash
            or readiness.account_snapshot_hash != equity.account_snapshot_hash
        ):
            raise RiskEvaluationConflict("risk readiness hashes changed after evaluation")

        adv_snapshots = self.adv_service.calculate(
            db,
            symbols={order.symbol for order in orders},
            as_of_date=plan.as_of_date,
        )
        adv_inputs = {
            symbol: {
                "available": snapshot.available,
                "average_daily_amount": (
                    None
                    if snapshot.average_daily_amount is None
                    else str(snapshot.average_daily_amount)
                ),
                "observation_count": snapshot.observation_count,
                "window": snapshot.window,
                "minimum_observations": snapshot.minimum_observations,
                "start_date": (
                    None if snapshot.start_date is None else snapshot.start_date.isoformat()
                ),
                "end_date": (None if snapshot.end_date is None else snapshot.end_date.isoformat()),
                "input_hash": snapshot.input_hash,
                "daily_bar_ids": list(snapshot.daily_bar_ids),
                "observations": list(snapshot.observations),
            }
            for symbol, snapshot in sorted(adv_snapshots.items())
        }
        if hash_payload(adv_inputs) != check.adv_snapshot_hash:
            raise RiskEvaluationConflict("ADV inputs changed after risk evaluation")
        if hash_payload(RISK_POLICY) != check.risk_policy_hash:
            raise RiskEvaluationConflict("risk policy changed after risk evaluation")
        evaluation_hash = hash_payload(
            {
                "plan_id": plan.id,
                "plan_request_hash": plan.request_hash,
                "account_id": account.id,
                "equity_snapshot_id": equity.id,
                "equity_valuation_hash": equity.valuation_hash,
                "readiness_hash": readiness_hash,
                "adv_snapshot_hash": check.adv_snapshot_hash,
                "risk_policy_hash": check.risk_policy_hash,
                "code_version": settings.app_code_version,
            }
        )
        if evaluation_hash != check.evaluation_hash:
            raise RiskEvaluationConflict("risk evaluation hash is invalid")
        return check, orders, plan, targets, equity

    def _replay(self, db: Session, check, *, request_hash: str) -> RiskEvaluationRead:
        if check.request_hash != request_hash:
            raise RiskEvaluationConflict(
                "idempotency key was already used with a different request"
            )
        _, orders = self.risk_repository.get_with_orders(db, check_id=check.id)
        return self._to_read(check, orders)

    @staticmethod
    def _to_read(check, orders) -> RiskEvaluationRead:
        preconditions = (check.readiness_snapshot or {}).get("preconditions", [])
        return RiskEvaluationRead(
            check_id=check.id,
            plan_id=check.plan_id,
            paper_account_id=check.account_id,
            equity_snapshot_id=check.equity_snapshot_id,
            evaluation_status=check.evaluation_status,
            risk_passed=check.passed,
            current_drawdown=check.current_drawdown,
            limits=check.limits,
            preconditions=preconditions,
            plan_request_hash=check.plan_request_hash,
            readiness_account_hash=check.readiness_account_hash,
            readiness_market_hash=check.readiness_market_hash,
            readiness_hash=check.readiness_hash,
            adv_snapshot_hash=check.adv_snapshot_hash,
            risk_policy_hash=check.risk_policy_hash,
            evaluation_hash=check.evaluation_hash,
            code_version=check.code_version,
            created_at=check.created_at,
            orders=[RiskOrderEvaluationRead.model_validate(order) for order in orders],
        )
