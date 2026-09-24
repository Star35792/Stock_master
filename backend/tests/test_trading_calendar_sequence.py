from datetime import date

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.data_sources import TradingDayRecord
from app.database.session import initialize_database
from app.models.market import TradingCalendar
from app.repositories.market.trading_calendar import TradingCalendarRepository


def test_calendar_upsert_survives_legacy_ids_ahead_of_sequence(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'legacy-calendar.duckdb'}")
    initialize_database(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO market.trading_calendar "
                "(id, exchange, trade_date, is_open, is_half_day, source_name) "
                "VALUES (2, 'SH', '2026-09-21', true, false, 'legacy')"
            )
        )
    with Session(engine) as db:
        TradingCalendarRepository().upsert_many(
            db,
            records=[
                TradingDayRecord(exchange="SH", trade_date=date(2026, 9, 21)),
                TradingDayRecord(exchange="SH", trade_date=date(2026, 9, 22)),
            ],
            source_name="hithink_finance",
        )
        db.commit()
        assert db.scalar(select(func.count()).select_from(TradingCalendar)) == 2
        db.add(TradingCalendar(
            exchange="SZ", trade_date=date(2026, 9, 23), is_open=True,
        ))
        db.commit()
        assert db.scalar(select(func.count()).select_from(TradingCalendar)) == 3
    engine.dispose()
