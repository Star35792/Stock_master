from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.models.portfolio import PortfolioPlanRecord, PortfolioTargetRecord
from app.portfolio import PortfolioPlan
from app.portfolio.style_risk import PortfolioStyleRisk


def _style_risk_json(value: PortfolioStyleRisk | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "style_exposures": [
            {
                "style_name": exposure.style_name,
                "portfolio_exposure": str(exposure.portfolio_exposure),
                "benchmark_exposure": (
                    str(exposure.benchmark_exposure)
                    if exposure.benchmark_exposure is not None else None
                ),
                "active_exposure": (
                    str(exposure.active_exposure)
                    if exposure.active_exposure is not None else None
                ),
            }
            for exposure in value.style_exposures
        ],
        "tracking_error": str(value.tracking_error) if value.tracking_error is not None else None,
        "portfolio_volatility": (
            str(value.portfolio_volatility) if value.portfolio_volatility is not None else None
        ),
        "missing_styles": list(value.missing_styles),
        "missing_benchmark": value.missing_benchmark,
        "factor_versions": value.factor_versions,
        "factor_evidence": value.factor_evidence,
        "factor_as_of_date": (
            value.factor_as_of_date.isoformat() if value.factor_as_of_date is not None else None
        ),
        "excluded_styles": list(value.excluded_styles),
        "sample_start_date": (
            value.sample_start_date.isoformat() if value.sample_start_date is not None else None
        ),
        "sample_end_date": (
            value.sample_end_date.isoformat() if value.sample_end_date is not None else None
        ),
        "return_observation_count": value.return_observation_count,
    }


class PortfolioPlanRepository:
    def list_plans(
        self, db: Session, *, limit: int, offset: int
    ) -> tuple[list[PortfolioPlanRecord], int]:
        total = db.scalar(select(func.count()).select_from(PortfolioPlanRecord)) or 0
        rows = list(
            db.scalars(
                select(PortfolioPlanRecord)
                .options(
                    load_only(
                        PortfolioPlanRecord.id,
                        PortfolioPlanRecord.name,
                        PortfolioPlanRecord.as_of_date,
                        PortfolioPlanRecord.total_equity,
                        PortfolioPlanRecord.code_version,
                        PortfolioPlanRecord.created_at,
                        raiseload=True,
                    )
                )
                .order_by(PortfolioPlanRecord.created_at.desc(), PortfolioPlanRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return rows, total

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
            style_risk=_style_risk_json(plan.style_risk),
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
