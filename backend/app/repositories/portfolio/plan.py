from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.portfolio import PortfolioPlanRecord, PortfolioTargetRecord
from app.portfolio import PortfolioPlan


class PortfolioPlanRepository:
    def get_by_idempotency_key(
        self,
        db: Session,
        *,
        idempotency_key: str,
    ) -> PortfolioPlanRecord | None:
        return db.scalar(
            select(PortfolioPlanRecord).where(
                PortfolioPlanRecord.idempotency_key == idempotency_key
            )
        )

    def get_with_targets(
        self,
        db: Session,
        *,
        plan_id: int,
    ) -> tuple[PortfolioPlanRecord | None, list[PortfolioTargetRecord]]:
        plan = db.get(PortfolioPlanRecord, plan_id)
        targets = list(
            db.scalars(
                select(PortfolioTargetRecord)
                .where(PortfolioTargetRecord.plan_id == plan_id)
                .order_by(PortfolioTargetRecord.symbol)
            )
        )
        return plan, targets

    def save(
        self,
        db: Session,
        *,
        name: str,
        as_of_date: date,
        parameters: dict[str, Any],
        plan: PortfolioPlan,
        code_version: str,
        idempotency_key: str,
        request_hash: str,
        input_snapshot: dict[str, Any],
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
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_snapshot=input_snapshot,
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
                    score=target.score,
                    reasons=list(target.reasons),
                )
                for target in plan.targets
            ]
        )
        return record
