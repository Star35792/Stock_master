from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.factor import FactorProfile


class FactorProfileRepository:
    def upsert(
        self,
        db: Session,
        *,
        factor_name: str,
        factor_version: str,
        trade_date: date,
        universe_size: int,
        observation_count: int,
        missing_count: int,
        coverage_rate: Decimal,
        missing_rate: Decimal,
        mean: Decimal | None,
        standard_deviation: Decimal | None,
        minimum: Decimal | None,
        maximum: Decimal | None,
        p01: Decimal | None,
        p99: Decimal | None,
        extreme_count: int,
        ic_1d: Decimal | None,
        rank_ic_1d: Decimal | None,
        top_quantile_turnover: Decimal | None,
        code_version: str,
    ) -> None:
        row = {
            "factor_name": factor_name,
            "factor_version": factor_version,
            "trade_date": trade_date,
            "universe_size": universe_size,
            "observation_count": observation_count,
            "missing_count": missing_count,
            "coverage_rate": coverage_rate,
            "missing_rate": missing_rate,
            "mean": mean,
            "standard_deviation": standard_deviation,
            "minimum": minimum,
            "maximum": maximum,
            "p01": p01,
            "p99": p99,
            "extreme_count": extreme_count,
            "ic_1d": ic_1d,
            "rank_ic_1d": rank_ic_1d,
            "top_quantile_turnover": top_quantile_turnover,
            "code_version": code_version,
        }
        statement = insert(FactorProfile).values(row)
        statement = statement.on_conflict_do_update(
            index_elements=[
                FactorProfile.factor_name,
                FactorProfile.factor_version,
                FactorProfile.trade_date,
            ],
            set_={
                key: getattr(statement.excluded, key)
                for key in row
                if key not in {"factor_name", "factor_version", "trade_date"}
            },
        )
        db.execute(statement)

    def list(
        self,
        db: Session,
        *,
        factor_name: str | None = None,
        factor_version: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[FactorProfile], int]:
        filters = []
        if factor_name:
            filters.append(FactorProfile.factor_name == factor_name)
        if factor_version:
            filters.append(FactorProfile.factor_version == factor_version)
        if start_date:
            filters.append(FactorProfile.trade_date >= start_date)
        if end_date:
            filters.append(FactorProfile.trade_date <= end_date)

        total = db.scalar(select(func.count()).select_from(FactorProfile).where(*filters)) or 0
        items = list(
            db.scalars(
                select(FactorProfile)
                .where(*filters)
                .order_by(FactorProfile.trade_date, FactorProfile.factor_name)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
