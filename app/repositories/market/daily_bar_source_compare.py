from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import DailyBarSourceCompare


class DailyBarSourceCompareRepository:
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
