from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.market import TradingCalendar
from app.models.raw import ImportBatch, RawDailyBar
from app.services.hithink_dump_collection import HithinkDumpCollectionService


@pytest.fixture
def db():
    engine = create_engine("duckdb:///:memory:")
    initialize_database(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(ImportBatch(
            batch_no="retry", source_name="hithink_finance_dump", source_type="vendor",
            dataset="daily_bar", status="success",
            request_params={"start_date": "2026-09-21", "end_date": "2026-09-22"},
        ))
        for day in (21, 22):
            session.add(TradingCalendar(exchange="SH", trade_date=date(2026, 9, day), is_open=True))
        session.commit()
        yield session
    engine.dispose()


def add_raw(db, *, day=21, close="10", high="12", source="hithink_finance_dump"):
    row = RawDailyBar(
        batch_id=999, source_name=source, source_type="vendor", source_priority=40,
        symbol="600000.SH", security_type="stock", trade_date=date(2026, 9, day),
        adjust_type="none", open=Decimal("10"), high=Decimal(high), low=Decimal("9"),
        close=Decimal(close), volume=Decimal("100"), amount=Decimal("1000"),
    )
    db.add(row)
    db.commit()
    return row.id


def test_retry_recovers_existing_raw_and_chooses_latest_version(db):
    add_raw(db)
    latest = add_raw(db, close="11")
    add_raw(db, close="12", source="other")
    service = HithinkDumpCollectionService()
    assert service.build_market_daily(db, batch_no="retry") == 1
    assert db.execute(text("SELECT close, selected_raw_id FROM market.daily_bar")).one() == (
        Decimal("11"), latest,
    )
    # Repeating the build leaves existing quality assessment untouched.
    db.execute(text("UPDATE market.daily_bar SET quality_score=75"))
    db.commit()
    assert service.build_market_daily(db, batch_no="retry") == 1
    assert db.scalar(text("SELECT count(*) FROM market.daily_bar")) == 1
    assert db.scalar(text("SELECT quality_score FROM market.daily_bar")) == Decimal("75")


def test_retry_protects_manual_repairs(db):
    original = add_raw(db)
    service = HithinkDumpCollectionService()
    service.build_market_daily(db, batch_no="retry")
    db.execute(text("UPDATE market.daily_bar SET quality_status='manual_fixed'"))
    db.commit()
    add_raw(db, close="11")
    service.build_market_daily(db, batch_no="retry")
    assert db.scalar(text("SELECT selected_raw_id FROM market.daily_bar")) == original


def test_build_caps_market_date_but_preserves_raw(db):
    add_raw(db, day=21)
    add_raw(db, day=22)
    service = HithinkDumpCollectionService()
    assert service.build_market_daily(db, batch_no="retry", end_date=date(2026, 9, 21)) == 1
    assert db.scalar(text("SELECT max(trade_date) FROM market.daily_bar")) == date(2026, 9, 21)
    assert db.scalar(text("SELECT count(*) FROM raw.daily_bar")) == 2
    assert service.build_market_daily(db, batch_no="retry") == 2


def test_newest_invalid_version_does_not_revive_older_valid_raw(db):
    add_raw(db)
    add_raw(db, close="13", high="12")
    assert HithinkDumpCollectionService().build_market_daily(db, batch_no="retry") == 0
    assert db.scalar(text("SELECT count(*) FROM market.daily_bar")) == 0


def test_old_batch_without_window_cannot_roll_back_newer_selected_raw(db):
    old = add_raw(db)
    newest = add_raw(db, close="11")
    service = HithinkDumpCollectionService()
    service.build_market_daily(db, batch_no="retry")
    db.add(ImportBatch(
        id=999, batch_no="legacy", source_name="hithink_finance_dump", source_type="vendor",
        dataset="daily_bar", status="success", request_params={},
    ))
    db.execute(text("UPDATE raw.daily_bar SET batch_id=1000 WHERE id=:id"), {"id": newest})
    db.commit()
    service.build_market_daily(db, batch_no="legacy")
    assert db.scalar(text("SELECT selected_raw_id FROM market.daily_bar")) == newest
    assert old < newest


def test_auto_sync_real_parquet_repeats_and_recovers_market_gap(db, tmp_path):
    import duckdb
    from sqlalchemy import select

    from app.data_sources.hithink_dump import HithinkDump, HithinkDumpDataSource, HithinkDumpKind
    from app.models.system import JobLog
    from app.repositories.system.auto_data_sync import AUTO_DATA_SYNC_JOB, AutoDataSyncRepository
    from app.services.auto_data_sync import AutoDataSyncService

    paths = {
        HithinkDumpKind.FULL_DAILY: tmp_path / "full.parquet",
        HithinkDumpKind.RECENT_DAILY: tmp_path / "recent.parquet",
    }
    with duckdb.connect() as connection:
        for kind, path in paths.items():
            first_day = 20 if kind == HithinkDumpKind.FULL_DAILY else 21
            connection.execute(
                """
                CREATE OR REPLACE TABLE dump_rows AS
                    SELECT '600000.SH' AS thscode, 'CNY' AS currency,
                           '1d' AS interval, 'none' AS adjusted,
                           epoch_ms(CAST('2026-09-' || day AS TIMESTAMP)) AS date_ms,
                           10.0 AS open_price, 12.0 AS high_price, 9.0 AS low_price,
                           11.0 AS close_price, 100.0 AS volume, 1000.0 AS turnover
                    FROM range(?, 23) AS days(day)
                """,
                [first_day],
            )

            connection.execute("COPY dump_rows TO ? (FORMAT PARQUET)", [str(path)])

    class LocalDumpSource(HithinkDumpDataSource):
        def download(self, kind):
            return HithinkDump(kind=kind, path=paths[kind], release_tag=kind.value)

    class LocalReference:
        def sync_hithink_reference_data(self, session):
            for exchange in ("SH", "SZ", "BJ"):
                for day in (20, 21, 22):
                    existing = session.scalar(select(TradingCalendar.id).where(
                        TradingCalendar.exchange == exchange,
                        TradingCalendar.trade_date == date(2026, 9, day),
                    ))
                    if existing is None:
                        session.add(TradingCalendar(
                            exchange=exchange, trade_date=date(2026, 9, day), is_open=day != 20,
                        ))
            session.commit()

    service = AutoDataSyncService(
        collection=HithinkDumpCollectionService(data_source=LocalDumpSource()),
        reference=LocalReference(),
    )
    service.sync(db, cutoff=date(2026, 9, 22))
    raw_count = db.scalar(text("SELECT count(*) FROM raw.daily_bar"))
    assert raw_count == 3
    assert db.scalar(text("SELECT count(*) FROM market.daily_bar")) == 2
    service.sync(db, cutoff=date(2026, 9, 22))
    assert db.scalar(text("SELECT count(*) FROM raw.daily_bar")) == raw_count
    db.execute(text("DELETE FROM market.daily_bar WHERE trade_date='2026-09-21'"))
    db.commit()
    service.sync(db, cutoff=date(2026, 9, 22))
    assert db.scalar(text("SELECT count(*) FROM raw.daily_bar")) == raw_count
    assert db.scalar(text("SELECT count(*) FROM market.daily_bar")) == 2
    jobs = db.scalars(select(JobLog).where(JobLog.job_name == AUTO_DATA_SYNC_JOB)
                      .order_by(JobLog.id)).all()
    assert len(jobs) == 3
    assert all(job.status == "success" for job in jobs)
    assert [job.metadata_["mode"] for job in jobs] == ["full_then_recent", "recent", "recent"]
    assert all(job.metadata_["end_date"] == "2026-09-22" for job in jobs)
    assert jobs[-1].rows_inserted == 0
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 22)
