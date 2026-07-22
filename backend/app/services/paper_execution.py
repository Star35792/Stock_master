from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.repositories.trade import (
    PaperExecutionApprovalRepository,
    PaperExecutionBatchRepository,
    PaperTradingRepository,
)
from app.schemas.approval import PaperExecutionOrderRead, PaperExecutionRead
from app.services.paper_approval import (
    CONFIRMATION_POLICY,
    build_approval_hash,
    build_paper_order_batch,
)
from app.services.risk_evaluation import (
    BoundRiskEvaluationService,
    RiskEvaluationConflict,
    RiskEvaluationResourceNotFound,
    RiskEvaluationValidationError,
)
from app.services.snapshot_hashes import hash_paper_account_state, hash_payload
from app.trade import BrokerFill, BrokerOrderRequest, PaperBrokerAdapter

EXECUTION_POLICY = {
    "name": "deterministic_full_fill_paper_execution",
    "version": "v1",
    "code_version": settings.app_code_version,
    "execution_mode": "paper",
    "execution_order": "risk_reducing_sells_then_risk_increasing_buys",
    "atomicity": "all_orders",
    "commission_rate": "0.0003",
    "slippage_bps": "2",
    "average_cost_basis": "fill_price_including_buy_commission",
    "sellability_policy": "same_day_buys_not_sellable_for_all_securities",
}


class PaperExecutionConflict(ValueError):
    def __init__(self, message: str, *, existing_execution_id: int | None = None) -> None:
        super().__init__(message)
        self.existing_execution_id = existing_execution_id


class PaperExecutionNotFound(ValueError):
    pass


@dataclass(frozen=True)
class PlannedFill:
    execution_index: int
    risk_check_order_id: int
    order: BrokerOrderRequest
    fill: BrokerFill
    cash_after: Decimal
    position_quantity: int
    average_cost: Decimal


class PaperExecutionBlocked(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PaperExecutionService:
    def __init__(
        self,
        *,
        approval_repository: PaperExecutionApprovalRepository | None = None,
        execution_repository: PaperExecutionBatchRepository | None = None,
        paper_repository: PaperTradingRepository | None = None,
        risk_service: BoundRiskEvaluationService | None = None,
    ) -> None:
        self.approval_repository = approval_repository or PaperExecutionApprovalRepository()
        self.execution_repository = execution_repository or PaperExecutionBatchRepository()
        self.paper_repository = paper_repository or PaperTradingRepository()
        self.risk_service = risk_service or BoundRiskEvaluationService()
        self.broker = PaperBrokerAdapter(
            commission_rate=Decimal(EXECUTION_POLICY["commission_rate"]),
            slippage_bps=Decimal(EXECUTION_POLICY["slippage_bps"]),
        )

    def execute(
        self,
        db: Session,
        *,
        approval_id: int,
        expected_approval_hash: str,
        idempotency_key: str,
    ) -> PaperExecutionRead:
        request_hash = hash_payload(
            {
                "approval_id": approval_id,
                "expected_approval_hash": expected_approval_hash,
            }
        )
        existing = self.execution_repository.get_by_idempotency_key(
            db,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._replay(existing, request_hash=request_hash)

        approval = self.approval_repository.get_for_update(db, approval_id=approval_id)
        if approval is None:
            raise PaperExecutionNotFound("paper execution approval not found")
        if approval.approval_hash != expected_approval_hash:
            raise PaperExecutionConflict("acknowledged approval hash does not match")
        now = datetime.now(UTC)
        if approval.status != "pending":
            existing = self.execution_repository.get_by_idempotency_key(
                db,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._replay(existing, request_hash=request_hash)
            existing = self.execution_repository.get_by_approval_id(
                db,
                approval_id=approval.id,
            )
            raise PaperExecutionConflict(
                f"approval is {approval.status}",
                existing_execution_id=None if existing is None else existing.id,
            )
        if now >= approval.expires_at:
            raise PaperExecutionConflict("approval is expired")
        if (
            approval.approval_scope != "paper_execution"
            or approval.actor_type != "local_single_user"
        ):
            raise PaperExecutionConflict("approval is not valid for local paper execution")
        if (
            self.execution_repository.get_by_approval_id(
                db,
                approval_id=approval_id,
            )
            is not None
        ):
            raise PaperExecutionConflict("approval already has an execution batch")

        try:
            check, risk_orders, plan, targets, equity = (
                self.risk_service.validate_current_evaluation(
                    db,
                    check_id=approval.risk_check_id,
                )
            )
        except RiskEvaluationResourceNotFound as exc:
            raise PaperExecutionNotFound(str(exc)) from exc
        except (RiskEvaluationConflict, RiskEvaluationValidationError) as exc:
            raise PaperExecutionConflict(f"execution state drift: {exc}") from exc

        account = self.paper_repository.get_account_for_update(
            db,
            account_id=approval.account_id,
        )
        if account is None:
            raise PaperExecutionNotFound("paper account not found")
        positions = self.paper_repository.list_positions_for_update(
            db,
            account_id=account.id,
        )
        account_state_hash_before = hash_paper_account_state(
            account.id,
            account.cash_balance,
            positions,
        )
        rebuilt_summary, _, rebuilt_order_hash = build_paper_order_batch(
            check=check,
            orders=risk_orders,
            plan=plan,
            targets=targets,
            equity=equity,
            account=account,
            broker=self.broker,
        )
        rebuilt_approval_hash = build_approval_hash(
            check=check,
            equity=equity,
            order_batch_hash=rebuilt_order_hash,
            approved_at=approval.approved_at,
            expires_at=approval.expires_at,
        )
        if (
            rebuilt_order_hash != approval.order_batch_hash
            or rebuilt_summary != approval.order_summary
            or rebuilt_approval_hash != approval.approval_hash
            or approval.plan_id != check.plan_id
            or approval.account_id != check.account_id
            or approval.equity_snapshot_id != equity.id
            or check.evaluation_hash != approval.evaluation_hash
            or check.plan_request_hash != approval.plan_request_hash
            or check.readiness_account_hash != approval.readiness_account_hash
            or check.readiness_market_hash != approval.readiness_market_hash
            or check.readiness_hash != approval.readiness_hash
            or check.adv_snapshot_hash != approval.adv_snapshot_hash
            or check.risk_policy_hash != approval.risk_policy_hash
            or equity.valuation_hash != approval.equity_valuation_hash
            or check.code_version != approval.code_version
            or approval.code_version != settings.app_code_version
        ):
            raise PaperExecutionConflict("approved order batch changed before execution")
        self._validate_policy(approval.confirmation_policy)

        started_at = datetime.now(UTC)
        try:
            planned, cash_after, state_after, total_commission = self._preflight(
                db,
                account=account,
                positions=positions,
                plan=plan,
                risk_orders=risk_orders,
            )
        except PaperExecutionBlocked as exc:
            return self._save_rejected(
                db,
                approval=approval,
                request_hash=request_hash,
                idempotency_key=idempotency_key,
                account_state_hash_before=account_state_hash_before,
                cash_before=Decimal(account.cash_balance),
                failure_code=exc.code,
                failure_message=str(exc),
                started_at=started_at,
            )

        account_state_hash_after = hash_paper_account_state(
            account.id,
            cash_after,
            list(state_after.values()),
        )
        completed_at = datetime.now(UTC)
        execution_policy_hash = hash_payload(EXECUTION_POLICY)
        values = {
            "approval_id": approval.id,
            "risk_check_id": approval.risk_check_id,
            "plan_id": approval.plan_id,
            "account_id": approval.account_id,
            "equity_snapshot_id": approval.equity_snapshot_id,
            "status": "completed",
            "execution_mode": "paper",
            "actor_type": "local_single_user",
            "executed_by": "local_operator",
            "code_version": settings.app_code_version,
            "approval_hash": approval.approval_hash,
            "evaluation_hash": approval.evaluation_hash,
            "order_batch_hash": approval.order_batch_hash,
            "execution_policy_hash": execution_policy_hash,
            "account_state_hash_before": account_state_hash_before,
            "account_state_hash_after": account_state_hash_after,
            "execution_hash": hash_payload({"request_hash": request_hash}),
            "cash_before": Decimal(account.cash_balance),
            "cash_after": cash_after,
            "order_count": len(planned),
            "fill_count": len(planned),
            "total_commission": total_commission,
            "input_snapshot": approval.order_summary,
            "result_snapshot": [],
            "execution_policy": EXECUTION_POLICY,
            "failure_code": None,
            "failure_message": None,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "started_at": started_at,
            "completed_at": completed_at,
        }
        try:
            with db.begin_nested():
                batch = self.execution_repository.save(db, values=values)
                results = []
                for item in planned:
                    order_record, fill_record = self.paper_repository.save_execution_fill(
                        db,
                        account=account,
                        plan_id=approval.plan_id,
                        check_id=approval.risk_check_id,
                        risk_check_order_id=item.risk_check_order_id,
                        execution_batch_id=batch.id,
                        execution_index=item.execution_index,
                        order=item.order,
                        fill=item.fill,
                        cash_balance=item.cash_after,
                        position_quantity=item.position_quantity,
                        average_cost=item.average_cost,
                    )
                    results.append(
                        {
                            "execution_index": item.execution_index,
                            "order_id": order_record.id,
                            "fill_id": fill_record.id,
                            "risk_check_order_id": item.risk_check_order_id,
                            "symbol": item.order.symbol,
                            "side": item.order.side,
                            "quantity": item.order.quantity,
                            "reference_price": str(item.order.reference_price),
                            "fill_price": str(item.fill.price),
                            "commission": str(item.fill.commission),
                            "cash_after": str(item.cash_after),
                        }
                    )
                batch.result_snapshot = results
                batch.execution_hash = hash_payload(
                    {
                        "approval_hash": approval.approval_hash,
                        "execution_policy_hash": execution_policy_hash,
                        "account_state_hash_before": account_state_hash_before,
                        "account_state_hash_after": account_state_hash_after,
                        "cash_before": str(values["cash_before"]),
                        "cash_after": str(cash_after),
                        "results": results,
                    }
                )
                approval.status = "consumed"
                approval.consumed_at = completed_at
        except IntegrityError as exc:
            existing = self.execution_repository.get_by_idempotency_key(
                db,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._replay(existing, request_hash=request_hash)
            raise PaperExecutionConflict("approval already has an execution batch") from exc
        return self._to_read(batch)

    def get(self, db: Session, *, execution_id: int) -> PaperExecutionRead:
        batch = self.execution_repository.get(db, execution_id=execution_id)
        if batch is None:
            raise PaperExecutionNotFound("paper execution batch not found")
        return self._to_read(batch)

    def get_by_approval(self, db: Session, *, approval_id: int) -> PaperExecutionRead:
        batch = self.execution_repository.get_by_approval_id(
            db,
            approval_id=approval_id,
        )
        if batch is None:
            raise PaperExecutionNotFound("paper execution batch not found")
        return self._to_read(batch)

    def _preflight(self, db, *, account, positions, plan, risk_orders):
        position_state = {
            position.symbol: {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "average_cost": Decimal(position.average_cost),
            }
            for position in positions
        }
        cash = Decimal(account.cash_balance)
        total_commission = Decimal()
        planned = []
        sell_lineage_checked = set()
        ordered_orders = sorted(
            risk_orders,
            key=lambda order: (0 if order.side == "sell" else 1, order.symbol),
        )
        for execution_index, risk_order in enumerate(ordered_orders):
            order = BrokerOrderRequest(
                symbol=risk_order.symbol,
                side=risk_order.side,
                quantity=abs(risk_order.order_quantity),
                reference_price=Decimal(risk_order.reference_price),
                trade_date=plan.as_of_date,
            )
            fill = self.broker.execute(order)
            current = position_state.get(
                order.symbol,
                {"symbol": order.symbol, "quantity": 0, "average_cost": Decimal()},
            )
            amount = Decimal(fill.quantity) * fill.price
            if order.side == "sell":
                if order.symbol not in sell_lineage_checked:
                    if (
                        self.paper_repository.net_filled_quantity(
                            db,
                            account_id=account.id,
                            symbol=order.symbol,
                        )
                        != current["quantity"]
                    ):
                        raise PaperExecutionBlocked(
                            "unknown_position_sellability",
                            f"{order.symbol} position lacks complete fill lineage",
                        )
                    sell_lineage_checked.add(order.symbol)
                bought_today = self.paper_repository.bought_quantity_on_date(
                    db,
                    account_id=account.id,
                    symbol=order.symbol,
                    trade_date=plan.as_of_date,
                )
                available_quantity = max(0, current["quantity"] - bought_today)
                if fill.quantity > available_quantity:
                    raise PaperExecutionBlocked(
                        "insufficient_available_position",
                        f"{order.symbol} sell quantity exceeds conservative T+1 availability",
                    )
                quantity_after = current["quantity"] - fill.quantity
                average_cost = Decimal() if quantity_after == 0 else current["average_cost"]
                cash += amount - fill.commission
            else:
                required_cash = amount + fill.commission
                if cash < required_cash:
                    raise PaperExecutionBlocked(
                        "insufficient_cash",
                        f"{order.symbol} buy requires more cash than the simulated account has",
                    )
                quantity_after = current["quantity"] + fill.quantity
                average_cost = (
                    Decimal(current["quantity"]) * current["average_cost"]
                    + amount
                    + fill.commission
                ) / Decimal(quantity_after)
                cash -= required_cash
            cash = cash.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            average_cost = average_cost.quantize(
                Decimal("0.000001"),
                rounding=ROUND_HALF_UP,
            )
            current = {
                "symbol": order.symbol,
                "quantity": quantity_after,
                "average_cost": average_cost,
            }
            position_state[order.symbol] = current
            total_commission += fill.commission
            total_commission = total_commission.quantize(
                Decimal("0.000001"),
                rounding=ROUND_HALF_UP,
            )
            planned.append(
                PlannedFill(
                    execution_index=execution_index,
                    risk_check_order_id=risk_order.id,
                    order=order,
                    fill=fill,
                    cash_after=cash,
                    position_quantity=quantity_after,
                    average_cost=average_cost,
                )
            )
        return planned, cash, position_state, total_commission

    def _save_rejected(
        self,
        db,
        *,
        approval,
        request_hash,
        idempotency_key,
        account_state_hash_before,
        cash_before,
        failure_code,
        failure_message,
        started_at,
    ):
        completed_at = datetime.now(UTC)
        execution_policy_hash = hash_payload(EXECUTION_POLICY)
        execution_hash = hash_payload(
            {
                "approval_hash": approval.approval_hash,
                "execution_policy_hash": execution_policy_hash,
                "failure_code": failure_code,
                "account_state_hash_before": account_state_hash_before,
            }
        )
        try:
            with db.begin_nested():
                batch = self.execution_repository.save(
                    db,
                    values={
                        "approval_id": approval.id,
                        "risk_check_id": approval.risk_check_id,
                        "plan_id": approval.plan_id,
                        "account_id": approval.account_id,
                        "equity_snapshot_id": approval.equity_snapshot_id,
                        "status": "rejected",
                        "execution_mode": "paper",
                        "actor_type": "local_single_user",
                        "executed_by": "local_operator",
                        "code_version": settings.app_code_version,
                        "approval_hash": approval.approval_hash,
                        "evaluation_hash": approval.evaluation_hash,
                        "order_batch_hash": approval.order_batch_hash,
                        "execution_policy_hash": execution_policy_hash,
                        "account_state_hash_before": account_state_hash_before,
                        "account_state_hash_after": account_state_hash_before,
                        "execution_hash": execution_hash,
                        "cash_before": cash_before,
                        "cash_after": cash_before,
                        "order_count": len(approval.order_summary),
                        "fill_count": 0,
                        "total_commission": Decimal(),
                        "input_snapshot": approval.order_summary,
                        "result_snapshot": [],
                        "execution_policy": EXECUTION_POLICY,
                        "failure_code": failure_code,
                        "failure_message": failure_message,
                        "idempotency_key": idempotency_key,
                        "request_hash": request_hash,
                        "started_at": started_at,
                        "completed_at": completed_at,
                    },
                )
                approval.status = "consumed"
                approval.consumed_at = completed_at
        except IntegrityError as exc:
            existing = self.execution_repository.get_by_idempotency_key(
                db,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._replay(existing, request_hash=request_hash)
            existing = self.execution_repository.get_by_approval_id(
                db,
                approval_id=approval.id,
            )
            raise PaperExecutionConflict(
                "approval already has an execution batch",
                existing_execution_id=None if existing is None else existing.id,
            ) from exc
        return self._to_read(batch)

    @staticmethod
    def _validate_policy(confirmation_policy) -> None:
        for key in ("execution_mode", "execution_order", "commission_rate", "slippage_bps"):
            if confirmation_policy.get(key) != EXECUTION_POLICY[key]:
                raise PaperExecutionConflict("paper execution policy changed after approval")
        for key in CONFIRMATION_POLICY:
            if key not in confirmation_policy:
                raise PaperExecutionConflict("paper confirmation policy is incomplete")

    @staticmethod
    def _replay(batch, *, request_hash: str) -> PaperExecutionRead:
        if batch.request_hash != request_hash:
            raise PaperExecutionConflict(
                "idempotency key was already used with a different request"
            )
        return PaperExecutionService._to_read(batch)

    @staticmethod
    def _to_read(batch) -> PaperExecutionRead:
        return PaperExecutionRead(
            execution_id=batch.id,
            approval_id=batch.approval_id,
            risk_check_id=batch.risk_check_id,
            plan_id=batch.plan_id,
            account_id=batch.account_id,
            equity_snapshot_id=batch.equity_snapshot_id,
            status=batch.status,
            execution_mode=batch.execution_mode,
            actor_type=batch.actor_type,
            executed_by=batch.executed_by,
            code_version=batch.code_version,
            approval_hash=batch.approval_hash,
            evaluation_hash=batch.evaluation_hash,
            order_batch_hash=batch.order_batch_hash,
            execution_policy_hash=batch.execution_policy_hash,
            account_state_hash_before=batch.account_state_hash_before,
            account_state_hash_after=batch.account_state_hash_after,
            execution_hash=batch.execution_hash,
            cash_before=batch.cash_before,
            cash_after=batch.cash_after,
            order_count=batch.order_count,
            fill_count=batch.fill_count,
            total_commission=batch.total_commission,
            failure_code=batch.failure_code,
            failure_message=batch.failure_message,
            started_at=batch.started_at,
            completed_at=batch.completed_at,
            created_at=batch.created_at,
            orders=[PaperExecutionOrderRead.model_validate(item) for item in batch.result_snapshot],
        )
