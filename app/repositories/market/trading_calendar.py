from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.data_sources import TradingDayRecord
from app.models.market import TradingCalendar


class TradingCalendarRepository:
    def upsert_many(
        self,
        db: Session,
        *,
        records: list[TradingDayRecord],
        source_name: str,
    ) -> int:
        if not records:
            return 0

        rows = [
            {
                "exchange": record.exchange,
                "trade_date": record.trade_date,
                "is_open": record.is_open,
                "is_half_day": False,
                "source_name": source_name,
            }
            for record in records
        ]
        statement = insert(TradingCalendar).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[TradingCalendar.exchange, TradingCalendar.trade_date],
            set_={
                "is_open": statement.excluded.is_open,
                "is_half_day": statement.excluded.is_half_day,
                "source_name": statement.excluded.source_name,
            },
        )
        db.execute(statement)
        return len(rows)

    def get_open_dates(self, db: Session, *, exchange: str, dates: set) -> set:
        if not dates:
            return set()
        return set(
            db.scalars(
                select(TradingCalendar.trade_date).where(
                    TradingCalendar.exchange == exchange,
                    TradingCalendar.trade_date.in_(dates),
                    TradingCalendar.is_open.is_(True),
                )
            ).all()
        )

    def get_by_exchange_date(
        self,
        db: Session,
        *,
        exchange: str,
        trade_date: date,
    ) -> TradingCalendar | None:
        return db.scalar(
            select(TradingCalendar).where(
                TradingCalendar.exchange == exchange,
                TradingCalendar.trade_date == trade_date,
            )
        )

    def list(
        self,
        db: Session,
        *,
        exchange: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        is_open: bool | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[TradingCalendar], int]:
        filters = []
        if exchange:
            filters.append(TradingCalendar.exchange == exchange)
        if start_date:
            filters.append(TradingCalendar.trade_date >= start_date)
        if end_date:
            filters.append(TradingCalendar.trade_date <= end_date)
        if is_open is not None:
            filters.append(TradingCalendar.is_open == is_open)

        total = db.scalar(select(func.count()).select_from(TradingCalendar).where(*filters)) or 0
        items = list(
            db.scalars(
                select(TradingCalendar)
                .where(*filters)
                .order_by(TradingCalendar.exchange, TradingCalendar.trade_date)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
