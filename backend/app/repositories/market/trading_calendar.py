from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.data_sources import TradingDayRecord
from app.models.market import TradingCalendar
from app.repositories.market.sequence_ids import reserve_sequence_ids


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

        # Old bulk-loaded databases can have ids ahead of the DuckDB sequence.
        # A normal INSERT consumes nextval even for natural-key duplicates and
        # eventually collides with an existing primary key. Allocate ids only
        # for genuinely new calendar dates under the single-writer deployment.
        existing = set(db.execute(select(TradingCalendar.exchange, TradingCalendar.trade_date)))
        max_id = db.scalar(select(func.max(TradingCalendar.id))) or 0
        rows = []
        seen = set(existing)
        for record in records:
            key = (record.exchange, record.trade_date)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "exchange": record.exchange,
                    "trade_date": record.trade_date,
                    "is_open": record.is_open,
                    "is_half_day": False,
                    "source_name": source_name,
                }
            )
        ids = reserve_sequence_ids(
            db, sequence_name="market_trading_calendar_id_seq",
            max_existing_id=max_id, count=len(rows),
        )
        for row, row_id in zip(rows, ids, strict=True):
            row["id"] = row_id
        if not rows:
            return 0
        statement = insert(TradingCalendar).values(rows)
        # Trading days are immutable facts; DuckDB's ON CONFLICT DO UPDATE can
        # hit index-corruption bugs on this table, so we simply ignore duplicates.
        statement = statement.on_conflict_do_nothing(
            index_elements=[TradingCalendar.exchange, TradingCalendar.trade_date],
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

    def list_open_dates(
        self,
        db: Session,
        *,
        exchange: str,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        return list(
            db.scalars(
                select(TradingCalendar.trade_date)
                .where(
                    TradingCalendar.exchange == exchange,
                    TradingCalendar.trade_date >= start_date,
                    TradingCalendar.trade_date <= end_date,
                    TradingCalendar.is_open.is_(True),
                )
                .order_by(TradingCalendar.trade_date)
            )
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

    def get_many_on_date(
        self,
        db: Session,
        *,
        exchanges: set[str],
        trade_date: date,
    ) -> list[TradingCalendar]:
        if not exchanges:
            return []
        return list(
            db.scalars(
                select(TradingCalendar)
                .where(
                    TradingCalendar.exchange.in_(exchanges),
                    TradingCalendar.trade_date == trade_date,
                )
                .order_by(TradingCalendar.exchange)
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
