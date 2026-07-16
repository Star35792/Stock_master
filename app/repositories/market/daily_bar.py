from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import DailyBar


class DailyBarRepository:
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
