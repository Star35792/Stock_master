from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.risk import RiskCheck, RiskCheckOrder
from app.risk import OrderRiskDecision, RiskLimits


class RiskCheckRepository:
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
