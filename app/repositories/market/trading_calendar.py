from sqlalchemy import select
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
