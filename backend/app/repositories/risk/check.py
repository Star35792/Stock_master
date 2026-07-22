from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.risk import RiskCheck, RiskCheckOrder
from app.risk import OrderRiskDecision, RiskLimits


class RiskCheckRepository:
    def get_by_idempotency_key(
        self,
        db: Session,
        *,
        idempotency_key: str,
    ) -> RiskCheck | None:
        return db.scalar(select(RiskCheck).where(RiskCheck.idempotency_key == idempotency_key))

    def get_by_evaluation_hash(
        self,
        db: Session,
        *,
        evaluation_hash: str,
    ) -> RiskCheck | None:
        return db.scalar(select(RiskCheck).where(RiskCheck.evaluation_hash == evaluation_hash))

    def get_with_orders(
        self,
        db: Session,
        *,
        check_id: int,
    ) -> tuple[RiskCheck | None, list[RiskCheckOrder]]:
        check = db.get(RiskCheck, check_id)
        orders = list(
            db.scalars(
                select(RiskCheckOrder)
                .where(RiskCheckOrder.check_id == check_id)
                .order_by(RiskCheckOrder.symbol)
            )
        )
        return check, orders

    def save(
        self,
        db: Session,
        *,
        plan_id: int,
        current_drawdown: Decimal,
        limits: RiskLimits,
        decisions: list[OrderRiskDecision],
        code_version: str,
    ) -> RiskCheck:
        check = RiskCheck(
            plan_id=plan_id,
            current_drawdown=current_drawdown,
            limits={
                "max_symbol_weight": str(limits.max_symbol_weight),
                "max_industry_weight": str(limits.max_industry_weight),
                "max_drawdown": str(limits.max_drawdown),
                "max_amount_participation": str(limits.max_amount_participation),
            },
            passed=all(decision.passed for decision in decisions),
            code_version=code_version,
        )
        db.add(check)
        db.flush()
        db.add_all(
            [
                RiskCheckOrder(
                    check_id=check.id,
                    symbol=decision.symbol,
                    side=decision.side,
                    order_quantity=decision.order_quantity,
                    passed=decision.passed,
                    rejection_reasons=list(decision.rejection_reasons),
                    rule_results=[
                        {
                            "rule_name": rule.rule_name,
                            "passed": rule.passed,
                            "reason_code": rule.reason_code,
                            "message": rule.message,
                        }
                        for rule in decision.rules
                    ],
                )
                for decision in decisions
            ]
        )
        return check

    def save_evaluation(
        self,
        db: Session,
        *,
        check_values: dict,
        order_values: list[dict],
    ) -> RiskCheck:
        check = RiskCheck(**check_values)
        db.add(check)
        db.flush()
        db.add_all([RiskCheckOrder(check_id=check.id, **values) for values in order_values])
        return check
