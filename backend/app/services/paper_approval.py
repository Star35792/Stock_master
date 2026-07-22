from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.repositories.trade import (
    PaperExecutionApprovalRepository,
    PaperTradingRepository,
)
from app.schemas.approval import PaperExecutionApprovalRead
from app.services.risk_evaluation import (
    BoundRiskEvaluationService,
    RiskEvaluationConflict,
    RiskEvaluationResourceNotFound,
    RiskEvaluationValidationError,
)
from app.services.snapshot_hashes import hash_payload
from app.trade import BrokerOrderRequest, PaperBrokerAdapter

APPROVAL_TTL = timedelta(minutes=5)
CONFIRMATION_POLICY = {
    "name": "paper_execution_confirmation",
    "version": "v1",
    "ttl_seconds": 300,
    "execution_mode": "paper",
    "actor_type": "local_single_user",
    "code_version": settings.app_code_version,
    "execution_order": "risk_reducing_sells_then_risk_increasing_buys",
    "commission_rate": "0.0003",
    "slippage_bps": "2",
}


class PaperApprovalConflict(ValueError):
    pass


class PaperApprovalValidationError(ValueError):
    pass


class PaperApprovalNotFound(ValueError):
    pass


def _decimal_text(value) -> str:
    return format(Decimal(value).normalize(), "f")


def build_paper_order_batch(*, check, orders, plan, targets, equity, account, broker):
    target_by_symbol = {target.symbol: target for target in targets}
    ordered_orders = sorted(
        orders,
        key=lambda order: (0 if order.side == "sell" else 1, order.symbol),
    )
    order_summary = []
    estimated_cash = Decimal(equity.cash_balance)
    for execution_index, order in enumerate(ordered_orders):
        target = target_by_symbol[order.symbol]
        fill = broker.execute(
            BrokerOrderRequest(
                symbol=order.symbol,
                side=order.side,
                quantity=abs(order.order_quantity),
                reference_price=order.reference_price,
                trade_date=plan.as_of_date,
            )
        )
        estimated_amount = Decimal(fill.quantity) * fill.price
        if order.side == "sell":
            estimated_cash += estimated_amount - fill.commission
        else:
            estimated_cash -= estimated_amount + fill.commission
        estimated_cash = estimated_cash.quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )
        order_summary.append(
            {
                "execution_index": execution_index,
                "risk_check_order_id": getattr(order, "id", None),
                "symbol": order.symbol,
                "security_type": target.security_type,
                "industry": target.industry,
                "side": order.side,
                "signed_quantity": order.order_quantity,
                "reference_price": _decimal_text(order.reference_price),
                "estimated_fill_price": _decimal_text(fill.price),
                "estimated_amount": _decimal_text(estimated_amount),
                "estimated_commission": _decimal_text(fill.commission),
                "current_quantity": target.current_quantity,
                "target_quantity": target.target_quantity,
                "current_symbol_weight": _decimal_text(order.current_symbol_weight),
                "projected_symbol_weight": _decimal_text(order.projected_symbol_weight),
                "current_industry_weight": _decimal_text(order.current_industry_weight),
                "projected_industry_weight": _decimal_text(order.projected_industry_weight),
                "risk_classification": (
                    "risk_reducing" if order.side == "sell" else "risk_increasing"
                ),
                "average_daily_amount": (
                    None
                    if order.average_daily_amount is None
                    else _decimal_text(order.average_daily_amount)
                ),
                "adv_window": order.adv_window,
                "adv_observation_count": order.adv_observation_count,
                "rule_results": order.rule_results,
            }
        )
    order_batch_hash = hash_payload(
        {
            "execution_mode": "paper",
            "account_id": account.id,
            "plan_id": plan.id,
            "risk_check_id": check.id,
            "valuation_date": plan.as_of_date.isoformat(),
            "orders": order_summary,
            "estimated_cash_after": _decimal_text(estimated_cash),
            "confirmation_policy": CONFIRMATION_POLICY,
        }
    )
    return order_summary, estimated_cash, order_batch_hash


def build_approval_hash(*, check, equity, order_batch_hash, approved_at, expires_at):
    return hash_payload(
        {
            "risk_check_id": check.id,
            "plan_id": check.plan_id,
            "account_id": check.account_id,
            "equity_snapshot_id": equity.id,
            "evaluation_hash": check.evaluation_hash,
            "plan_request_hash": check.plan_request_hash,
            "equity_valuation_hash": equity.valuation_hash,
            "readiness_account_hash": check.readiness_account_hash,
            "readiness_market_hash": check.readiness_market_hash,
            "readiness_hash": check.readiness_hash,
            "adv_snapshot_hash": check.adv_snapshot_hash,
            "risk_policy_hash": check.risk_policy_hash,
            "order_batch_hash": order_batch_hash,
            "approval_scope": "paper_execution",
            "actor_type": "local_single_user",
            "approved_by": "local_operator",
            "code_version": check.code_version,
            "approved_at": approved_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "confirmation_policy": CONFIRMATION_POLICY,
        }
    )


class PaperExecutionApprovalService:
    def __init__(
        self,
        *,
        repository: PaperExecutionApprovalRepository | None = None,
        risk_service: BoundRiskEvaluationService | None = None,
        paper_repository: PaperTradingRepository | None = None,
        broker: PaperBrokerAdapter | None = None,
    ) -> None:
        self.repository = repository or PaperExecutionApprovalRepository()
        self.risk_service = risk_service or BoundRiskEvaluationService()
        self.paper_repository = paper_repository or PaperTradingRepository()
        self.broker = broker or PaperBrokerAdapter()

    def create(
        self,
        db: Session,
        *,
        risk_check_id: int,
        expected_evaluation_hash: str,
        confirmation_phrase: str,
        idempotency_key: str,
    ) -> PaperExecutionApprovalRead:
        request_hash = hash_payload(
            {
                "risk_check_id": risk_check_id,
                "expected_evaluation_hash": expected_evaluation_hash,
                "confirmation_phrase": confirmation_phrase,
            }
        )
        existing = self.repository.get_by_idempotency_key(
            db,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._replay(existing, request_hash=request_hash)
        expected_phrase = f"CONFIRM PAPER EXECUTION {risk_check_id}"
        if confirmation_phrase != expected_phrase:
            raise PaperApprovalValidationError(f"confirmation_phrase must equal {expected_phrase}")

        try:
            check, orders, plan, targets, equity = self.risk_service.validate_current_evaluation(
                db,
                check_id=risk_check_id,
            )
        except RiskEvaluationResourceNotFound as exc:
            raise PaperApprovalNotFound(str(exc)) from exc
        except (RiskEvaluationConflict, RiskEvaluationValidationError) as exc:
            raise PaperApprovalConflict(str(exc)) from exc
        if expected_evaluation_hash != check.evaluation_hash:
            raise PaperApprovalConflict("acknowledged evaluation hash does not match")
        if (
            self.repository.get_by_risk_check_id(
                db,
                risk_check_id=risk_check_id,
            )
            is not None
        ):
            raise PaperApprovalConflict(
                "this risk evaluation already has a paper execution approval"
            )

        account = self.paper_repository.get_account(
            db,
            account_id=check.account_id,
        )
        if account is None:
            raise PaperApprovalNotFound("paper account not found")
        order_summary, estimated_cash, order_batch_hash = build_paper_order_batch(
            check=check,
            orders=orders,
            plan=plan,
            targets=targets,
            equity=equity,
            account=account,
            broker=self.broker,
        )
        approved_at = datetime.now(UTC)
        expires_at = approved_at + APPROVAL_TTL
        approval_hash = build_approval_hash(
            check=check,
            equity=equity,
            order_batch_hash=order_batch_hash,
            approved_at=approved_at,
            expires_at=expires_at,
        )
        values = {
            "risk_check_id": check.id,
            "plan_id": plan.id,
            "account_id": account.id,
            "equity_snapshot_id": equity.id,
            "status": "pending",
            "approval_scope": "paper_execution",
            "actor_type": "local_single_user",
            "approved_by": "local_operator",
            "code_version": check.code_version,
            "evaluation_hash": check.evaluation_hash,
            "plan_request_hash": check.plan_request_hash,
            "equity_valuation_hash": equity.valuation_hash,
            "readiness_account_hash": check.readiness_account_hash,
            "readiness_market_hash": check.readiness_market_hash,
            "readiness_hash": check.readiness_hash,
            "adv_snapshot_hash": check.adv_snapshot_hash,
            "risk_policy_hash": check.risk_policy_hash,
            "order_batch_hash": order_batch_hash,
            "approval_hash": approval_hash,
            "order_summary": order_summary,
            "confirmation_policy": {
                **CONFIRMATION_POLICY,
                "estimated_cash_after": str(estimated_cash),
            },
            "approved_at": approved_at,
            "expires_at": expires_at,
            "consumed_at": None,
            "expired_at": None,
            "revoked_at": None,
            "revocation_reason": None,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "revocation_idempotency_key": None,
            "revocation_request_hash": None,
        }
        try:
            with db.begin_nested():
                approval = self.repository.save(db, values=values)
        except IntegrityError as exc:
            existing = self.repository.get_by_idempotency_key(
                db,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._replay(existing, request_hash=request_hash)
            raise PaperApprovalConflict(
                "this risk evaluation already has a paper execution approval"
            ) from exc
        return self._to_read(approval)

    def get(self, db: Session, *, approval_id: int) -> PaperExecutionApprovalRead:
        approval = self.repository.get(db, approval_id=approval_id)
        if approval is None:
            raise PaperApprovalNotFound("paper execution approval not found")
        return self._to_read(approval)

    def revoke(
        self,
        db: Session,
        *,
        approval_id: int,
        reason: str,
        idempotency_key: str,
    ) -> PaperExecutionApprovalRead:
        request_hash = hash_payload(
            {
                "approval_id": approval_id,
                "reason": reason,
            }
        )
        approval = self.repository.get_for_update(db, approval_id=approval_id)
        if approval is None:
            raise PaperApprovalNotFound("paper execution approval not found")
        if approval.revocation_idempotency_key == idempotency_key:
            if approval.revocation_request_hash != request_hash:
                raise PaperApprovalConflict(
                    "idempotency key was already used with a different revoke request"
                )
            return self._to_read(approval)
        now = datetime.now(UTC)
        if approval.status != "pending" or now >= approval.expires_at:
            raise PaperApprovalConflict("only a pending, unexpired approval can be revoked")
        approval.status = "revoked"
        approval.revoked_at = now
        approval.revocation_reason = reason
        approval.revocation_idempotency_key = idempotency_key
        approval.revocation_request_hash = request_hash
        return self._to_read(approval)

    @staticmethod
    def _replay(approval, *, request_hash: str) -> PaperExecutionApprovalRead:
        if approval.request_hash != request_hash:
            raise PaperApprovalConflict("idempotency key was already used with a different request")
        return PaperExecutionApprovalService._to_read(approval)

    @staticmethod
    def _to_read(approval) -> PaperExecutionApprovalRead:
        effective_status = approval.status
        if approval.status == "pending" and datetime.now(UTC) >= approval.expires_at:
            effective_status = "expired"
        return PaperExecutionApprovalRead(
            approval_id=approval.id,
            risk_check_id=approval.risk_check_id,
            plan_id=approval.plan_id,
            account_id=approval.account_id,
            equity_snapshot_id=approval.equity_snapshot_id,
            stored_status=approval.status,
            effective_status=effective_status,
            approval_scope=approval.approval_scope,
            actor_type=approval.actor_type,
            approved_by=approval.approved_by,
            code_version=approval.code_version,
            evaluation_hash=approval.evaluation_hash,
            plan_request_hash=approval.plan_request_hash,
            equity_valuation_hash=approval.equity_valuation_hash,
            readiness_account_hash=approval.readiness_account_hash,
            readiness_market_hash=approval.readiness_market_hash,
            readiness_hash=approval.readiness_hash,
            adv_snapshot_hash=approval.adv_snapshot_hash,
            risk_policy_hash=approval.risk_policy_hash,
            order_batch_hash=approval.order_batch_hash,
            approval_hash=approval.approval_hash,
            order_summary=approval.order_summary,
            confirmation_policy=approval.confirmation_policy,
            approved_at=approval.approved_at,
            expires_at=approval.expires_at,
            consumed_at=approval.consumed_at,
            revoked_at=approval.revoked_at,
            revocation_reason=approval.revocation_reason,
            created_at=approval.created_at,
            consumable_later=effective_status == "pending",
        )
