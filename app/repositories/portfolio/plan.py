from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models.portfolio import PortfolioPlanRecord, PortfolioTargetRecord
from app.portfolio import PortfolioPlan


class PortfolioPlanRepository:
    def save(
        self,
        db: Session,
        *,
        name: str,
        as_of_date: date,
        parameters: dict[str, Any],
        plan: PortfolioPlan,
        code_version: str,
    ) -> PortfolioPlanRecord:
        record = PortfolioPlanRecord(
            name=name,
            as_of_date=as_of_date,
            parameters=parameters,
            total_equity=plan.total_equity,
            cash_before=plan.cash_before,
            target_cash=plan.target_cash,
            cash_after=plan.cash_after,
            code_version=code_version,
        )
        db.add(record)
        db.flush()
        db.add_all(
            [
                PortfolioTargetRecord(
                    plan_id=record.id,
                    symbol=target.symbol,
                    security_type=target.security_type,
                    industry=target.industry,
                    requested_weight=target.requested_weight,
                    constrained_weight=target.constrained_weight,
                    final_weight=target.final_weight,
                    current_quantity=target.current_quantity,
                    target_quantity=target.target_quantity,
                    order_quantity=target.order_quantity,
                    price=target.price,
                    reasons=list(target.reasons),
                )
                for target in plan.targets
            ]
        )
        return record
