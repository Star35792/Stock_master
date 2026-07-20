from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import DailyBarSourceCompare


class DailyBarSourceCompareRepository:
    def list(
        self,
        db: Session,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str | None = None,
        is_conflict: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DailyBarSourceCompare], int]:
        filters = []
        if symbol:
            filters.append(DailyBarSourceCompare.symbol == symbol)
        if start_date:
            filters.append(DailyBarSourceCompare.trade_date >= start_date)
        if end_date:
            filters.append(DailyBarSourceCompare.trade_date <= end_date)
        if adjust_type:
            filters.append(DailyBarSourceCompare.adjust_type == adjust_type)
        if is_conflict is not None:
            filters.append(DailyBarSourceCompare.is_conflict == is_conflict)

        total = (
            db.scalar(select(func.count()).select_from(DailyBarSourceCompare).where(*filters))
            or 0
        )
        items = list(
            db.scalars(
                select(DailyBarSourceCompare)
                .where(*filters)
                .order_by(
                    DailyBarSourceCompare.trade_date.desc(),
                    DailyBarSourceCompare.symbol,
                    DailyBarSourceCompare.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def upsert_pair(
        self,
        db: Session,
        *,
        symbol: str,
        trade_date: date,
        adjust_type: str,
        source_a: str,
        source_b: str,
        raw_id_a: int,
        raw_id_b: int,
        close_diff: Decimal | None,
        close_diff_pct: Decimal | None,
        volume_diff_pct: Decimal | None,
        amount_diff_pct: Decimal | None,
        is_conflict: bool,
        conflict_reason: str | None,
    ) -> DailyBarSourceCompare:
        first_raw_id, second_raw_id = sorted((raw_id_a, raw_id_b))
        existing = db.scalar(
            select(DailyBarSourceCompare).where(
                DailyBarSourceCompare.raw_id_a == first_raw_id,
                DailyBarSourceCompare.raw_id_b == second_raw_id,
            )
        )
        if existing is None:
            existing = DailyBarSourceCompare(raw_id_a=first_raw_id, raw_id_b=second_raw_id)
            db.add(existing)

        existing.symbol = symbol
        existing.trade_date = trade_date
        existing.adjust_type = adjust_type
        existing.source_a = source_a
        existing.source_b = source_b
        existing.close_diff = close_diff
        existing.close_diff_pct = close_diff_pct
        existing.volume_diff_pct = volume_diff_pct
        existing.amount_diff_pct = amount_diff_pct
        existing.is_conflict = is_conflict
        existing.conflict_reason = conflict_reason
        return existing
