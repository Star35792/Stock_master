from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

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
        ic_5d: Decimal | None,
        rank_ic_5d: Decimal | None,
        ic_20d: Decimal | None,
        rank_ic_20d: Decimal | None,
        top_quantile_forward_return_1d: Decimal | None,
        bottom_quantile_forward_return_1d: Decimal | None,
        long_short_forward_return_1d: Decimal | None,
        long_short_forward_return_5d: Decimal | None = None,
        long_short_forward_return_20d: Decimal | None = None,
        quantile_monotonicity_1d: Decimal | None,
        top_quantile_turnover: Decimal | None,
        code_version: str,
        evidence_eligibility: str = "research_only",
        evidence_eligibility_reason: str = "unverified_evidence_eligibility",
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
            "ic_5d": ic_5d,
            "rank_ic_5d": rank_ic_5d,
            "ic_20d": ic_20d,
            "rank_ic_20d": rank_ic_20d,
            "top_quantile_forward_return_1d": top_quantile_forward_return_1d,
            "bottom_quantile_forward_return_1d": bottom_quantile_forward_return_1d,
            "long_short_forward_return_1d": long_short_forward_return_1d,
            "long_short_forward_return_5d": long_short_forward_return_5d,
            "long_short_forward_return_20d": long_short_forward_return_20d,
            "quantile_monotonicity_1d": quantile_monotonicity_1d,
            "top_quantile_turnover": top_quantile_turnover,
            "evidence_eligibility": evidence_eligibility,
            "evidence_eligibility_reason": evidence_eligibility_reason,
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

    def summarize(
        self,
        db: Session,
        *,
        factor_name: str | None = None,
        factor_version: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """按 (factor_name, factor_version) 聚合 IC 证据。

        只使用跨 DuckDB/PostgreSQL 均可移植的 ``count/min/max/avg`` 聚合，
        不做方言特定的标准差或百分位计算。
        """
        filters = []
        if factor_name:
            filters.append(FactorProfile.factor_name == factor_name)
        if factor_version:
            filters.append(FactorProfile.factor_version == factor_version)
        if start_date:
            filters.append(FactorProfile.trade_date >= start_date)
        if end_date:
            filters.append(FactorProfile.trade_date <= end_date)

        grouped = (
            select(
                FactorProfile.factor_name,
                FactorProfile.factor_version,
                func.count().label("trade_date_count"),
                func.min(FactorProfile.trade_date).label("first_trade_date"),
                func.max(FactorProfile.trade_date).label("last_trade_date"),
                # A factor version is not a complete evidence identity by
                # itself: the daily profiles must also come from one code
                # version.  Keep this in the aggregate instead of silently
                # averaging old and new diagnostic implementations.
                func.min(FactorProfile.code_version).label("code_version"),
                func.count(func.distinct(FactorProfile.code_version)).label(
                    "code_version_count"
                ),
                func.avg(FactorProfile.coverage_rate).label("mean_coverage_rate"),
                func.avg(FactorProfile.ic_1d).label("mean_ic_1d"),
                func.avg(FactorProfile.rank_ic_1d).label("mean_rank_ic_1d"),
                func.count(FactorProfile.rank_ic_1d).label(
                    "rank_ic_1d_observation_count"
                ),
                func.avg(FactorProfile.ic_5d).label("mean_ic_5d"),
                func.avg(FactorProfile.rank_ic_5d).label("mean_rank_ic_5d"),
                func.count(FactorProfile.rank_ic_5d).label(
                    "rank_ic_5d_observation_count"
                ),
                func.avg(FactorProfile.ic_20d).label("mean_ic_20d"),
                func.avg(FactorProfile.rank_ic_20d).label("mean_rank_ic_20d"),
                func.count(FactorProfile.rank_ic_20d).label(
                    "rank_ic_20d_observation_count"
                ),
                # Endpoint (long-short) aggregates.  The pool classifier gates
                # on these: a factor can carry a positive IC while its extreme
                # buckets still lose money, and such a factor is not tradable.
                func.avg(FactorProfile.long_short_forward_return_1d).label(
                    "mean_long_short_forward_return_1d"
                ),
                func.avg(FactorProfile.long_short_forward_return_5d).label(
                    "mean_long_short_forward_return_5d"
                ),
                func.avg(FactorProfile.long_short_forward_return_20d).label(
                    "mean_long_short_forward_return_20d"
                ),
                # How many days actually carry endpoint evidence -- distinguishes
                # "no endpoint evidence" from "negative endpoint".
                func.count(FactorProfile.long_short_forward_return_1d).label(
                    "endpoint_observation_count"
                ),
                func.avg(FactorProfile.quantile_monotonicity_1d).label(
                    "mean_quantile_monotonicity_1d"
                ),
                func.avg(FactorProfile.top_quantile_turnover).label(
                    "mean_top_quantile_turnover"
                ),
                # A mixed batch is strategy-eligible only when every daily
                # evidence row is eligible. The lexical minimum is deterministic
                # for the supported research_only/strategy_eligible values.
                func.min(FactorProfile.evidence_eligibility).label("evidence_eligibility"),
                func.min(FactorProfile.evidence_eligibility_reason).label(
                    "evidence_eligibility_reason"
                ),
            )
            .where(*filters)
            .group_by(FactorProfile.factor_name, FactorProfile.factor_version)
        )
        total = db.scalar(select(func.count()).select_from(grouped.subquery())) or 0
        rows = (
            db.execute(
                grouped.order_by(FactorProfile.factor_name, FactorProfile.factor_version)
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows], total
