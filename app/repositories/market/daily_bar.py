from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.market import DailyBar
from app.models.raw import RawDailyBar


class DailyBarRepository:
    def upsert_from_raw(
        self,
        db: Session,
        *,
        raw_bar: RawDailyBar,
        quality_score: Decimal,
        quality_status: str,
        is_suspect: bool,
    ) -> None:
        row = {
            "symbol": raw_bar.symbol,
            "security_type": raw_bar.security_type,
            "trade_date": raw_bar.trade_date,
            "adjust_type": raw_bar.adjust_type,
            "open": raw_bar.open,
            "high": raw_bar.high,
            "low": raw_bar.low,
            "close": raw_bar.close,
            "pre_close": raw_bar.pre_close,
            "change_amount": raw_bar.change_amount,
            "pct_change": raw_bar.pct_change,
            "volume": raw_bar.volume,
            "amount": raw_bar.amount,
            "turnover_rate": raw_bar.turnover_rate,
            "amplitude": raw_bar.amplitude,
            "source_name": raw_bar.source_name,
            "source_count": 1,
            "quality_score": quality_score,
            "quality_status": quality_status,
            "is_suspect": is_suspect,
            "selected_raw_id": raw_bar.id,
        }
        statement = insert(DailyBar).values(row)
        update_columns = {
            key: getattr(statement.excluded, key)
            for key in row
            if key not in {"symbol", "trade_date", "adjust_type"}
        }
        statement = statement.on_conflict_do_update(
            index_elements=[DailyBar.symbol, DailyBar.trade_date, DailyBar.adjust_type],
            set_=update_columns,
        )
        db.execute(statement)

    def list_by_symbol(
        self,
        db: Session,
        *,
        symbol: str,
        adjust_type: str,
        start_date: date | None = None,
        end_date: date | None = None,
        quality_status: str | None = "trusted",
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[DailyBar], int]:
        filters = [
            DailyBar.symbol == symbol,
            DailyBar.adjust_type == adjust_type,
        ]
        if start_date:
            filters.append(DailyBar.trade_date >= start_date)
        if end_date:
            filters.append(DailyBar.trade_date <= end_date)
        if quality_status:
            filters.append(DailyBar.quality_status == quality_status)

        total = db.scalar(select(func.count()).select_from(DailyBar).where(*filters)) or 0
        items = list(
            db.scalars(
                select(DailyBar)
                .where(*filters)
                .order_by(DailyBar.trade_date)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
