from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.market import Security, TradingCalendar
from app.models.raw import ImportBatch, RawDailyBar
from app.repositories.market import DailyBarRepository
from app.services.hithink_dump_collection import HithinkDumpCollectionService


@pytest.fixture
def db():
    engine = create_engine("duckdb:///:memory:")
    initialize_database(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(
            ImportBatch(
                batch_no="retry",
                source_name="hithink_finance_dump",
                source_type="vendor",
                dataset="daily_bar",
                status="success",
                request_params={"start_date": "2026-09-21", "end_date": "2026-09-22"},
            )
        )
        for day in (21, 22):
            session.add(TradingCalendar(exchange="SH", trade_date=date(2026, 9, day), is_open=True))
        session.commit()
        yield session
    engine.dispose()


def add_raw(
    db,
    *,
    day=21,
    close="10",
    high="12",
    source="hithink_finance_dump",
    volume="100",
    amount="1000",
    imported_at=None,
    is_suspended=False,
    pre_close=None,
    change_amount=None,
):
    row = RawDailyBar(
        batch_id=999,
        source_name=source,
        source_type="vendor",
        source_priority=40,
        symbol="600000.SH",
        security_type="stock",
        trade_date=date(2026, 9, day),
        adjust_type="none",
        open=Decimal("10"),
        high=Decimal(high),
        low=Decimal("9"),
        close=Decimal(close),
        volume=None if volume is None else Decimal(volume),
        amount=None if amount is None else Decimal(amount),
        imported_at=imported_at,
        is_suspended=is_suspended,
        pre_close=None if pre_close is None else Decimal(pre_close),
        change_amount=None if change_amount is None else Decimal(change_amount),
    )
    db.add(row)
    db.commit()
    return row.id


def test_retry_recovers_existing_raw_and_chooses_latest_version(db):
    add_raw(db)
    latest = add_raw(db, close="11")
    add_raw(db, close="11", source="other")
    service = HithinkDumpCollectionService()
    assert service.build_market_daily(db, batch_no="retry") == 1
    assert db.execute(text("SELECT close, selected_raw_id FROM market.daily_bar")).one() == (
        Decimal("11"),
        latest,
    )
    # Repeating the shared assessment restores the derived quality score.
    db.execute(text("UPDATE market.daily_bar SET quality_score=75"))
    db.commit()
    assert service.build_market_daily(db, batch_no="retry") == 1
    assert db.scalar(text("SELECT count(*) FROM market.daily_bar")) == 1
    assert db.scalar(text("SELECT quality_score FROM market.daily_bar")) == Decimal("100")


def test_historical_dump_does_not_copy_current_security_treatment_status(db):
    db.add(
        Security(
            symbol="600000.SH",
            raw_symbol="600000",
            name="ST当前名称",
            security_type="stock",
            exchange="SH",
            special_treatment_status="st",
            source_name="current_name",
        )
    )
    db.commit()
    add_raw(db)

    assert HithinkDumpCollectionService().build_market_daily(db, batch_no="retry") == 1
    assert db.scalar(
        text("SELECT special_treatment_status FROM market.daily_bar")
    ) is None


def test_retry_protects_manual_repairs(db):
    original = add_raw(db)
    service = HithinkDumpCollectionService()
    service.build_market_daily(db, batch_no="retry")
    db.execute(text("UPDATE market.daily_bar SET quality_status='manual_fixed'"))
    db.commit()
    add_raw(db, close="11")
    add_raw(db, close="12", source="tdx_official")
    service.build_market_daily(db, batch_no="retry")
    assert db.scalar(text("SELECT selected_raw_id FROM market.daily_bar")) == original
    assert db.scalar(text("SELECT quality_status FROM market.daily_bar")) == "manual_fixed"


def test_independent_source_conflict_survives_primary_revisions_and_can_recover(db):
    original = add_raw(db)
    service = HithinkDumpCollectionService()
    service.build_market_daily(db, batch_no="retry")
    assert db.scalar(text("SELECT quality_status FROM market.daily_bar")) == "trusted"

    add_raw(db, close="11", source="tdx_official")
    service.build_market_daily(db, batch_no="retry")
    assert db.execute(
        text("SELECT quality_status, is_suspect, selected_raw_id FROM market.daily_bar")
    ).one() == ("suspect", True, original)
    assert (
        DailyBarRepository().get_latest_consumable(db, symbol="600000.SH", adjust_type="none")
        is None
    )
    assert (
        db.scalar(text("SELECT count(*) FROM market.daily_bar_source_compare WHERE is_conflict"))
        == 1
    )
    assert (
        db.scalar(
            text(
                "SELECT count(*) FROM metadata.data_quality_issue "
                "WHERE issue_type='SOURCE_CONFLICT'"
            )
        )
        == 1
    )

    revised = add_raw(db, close="10.5")
    service.build_market_daily(db, batch_no="retry")
    assert db.scalar(text("SELECT quality_status FROM market.daily_bar")) == "suspect"
    service.build_market_daily(db, batch_no="retry")
    assert (
        db.scalar(
            text(
                "SELECT count(*) FROM metadata.data_quality_issue "
                "WHERE issue_type='SOURCE_CONFLICT'"
            )
        )
        == 2
    )

    add_raw(db, close="10.5", source="tdx_official")
    service.build_market_daily(db, batch_no="retry")
    assert db.execute(
        text("SELECT quality_status, is_suspect, selected_raw_id FROM market.daily_bar")
    ).one() == ("trusted", False, revised)


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


def test_fast_path_rechecks_historical_intraday_snapshot(db):
    add_raw(
        db,
        imported_at=datetime(2026, 9, 21, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert HithinkDumpCollectionService().build_market_daily(db, batch_no="retry") == 0
    assert db.execute(text("SELECT quality_status, is_suspect FROM market.daily_bar")).one() == (
        "suspect",
        True,
    )
    assert (
        db.scalar(
            text(
                "SELECT count(*) FROM metadata.data_quality_issue "
                "WHERE issue_type='INTRADAY_SNAPSHOT'"
            )
        )
        == 1
    )


def test_latest_suspended_revision_removes_existing_bar_from_trusted_reads(db):
    add_raw(db)
    service = HithinkDumpCollectionService()
    assert service.build_market_daily(db, batch_no="retry") == 1

    add_raw(db, is_suspended=True)

    assert service.build_market_daily(db, batch_no="retry") == 0
    assert db.execute(text("SELECT quality_status, is_suspect FROM market.daily_bar")).one() == (
        "rejected",
        True,
    )
    assert (
        db.scalar(
            text(
                "SELECT count(*) FROM metadata.data_quality_issue "
                "WHERE issue_type='SUSPENDED_BAR_REJECTED'"
            )
        )
        == 1
    )
    assert (
        DailyBarRepository().get_latest_consumable(db, symbol="600000.SH", adjust_type="none")
        is None
    )


def test_hard_rejection_does_not_override_manual_fixed_bar(db):
    original = add_raw(db)
    service = HithinkDumpCollectionService()
    service.build_market_daily(db, batch_no="retry")
    db.execute(
        text(
            "UPDATE market.daily_bar SET quality_status='manual_fixed', "
            "is_suspect=false, quality_score=100"
        )
    )
    db.commit()

    add_raw(db, is_suspended=True)
    service.build_market_daily(db, batch_no="retry")

    assert db.execute(
        text(
            "SELECT quality_status, is_suspect, quality_score, selected_raw_id "
            "FROM market.daily_bar"
        )
    ).one() == ("manual_fixed", False, Decimal("100.00"), original)


def test_closed_calendar_raw_is_rejected_with_issue(db):
    db.execute(
        text(
            "UPDATE market.trading_calendar SET is_open=false "
            "WHERE exchange='SH' AND trade_date='2026-09-21'"
        )
    )
    db.commit()
    add_raw(db)

    assert HithinkDumpCollectionService().build_market_daily(db, batch_no="retry") == 0
    assert db.scalar(text("SELECT count(*) FROM market.daily_bar")) == 0
    assert db.scalar(
        text(
            "SELECT count(*) FROM metadata.data_quality_issue "
            "WHERE issue_type='CALENDAR_MISMATCH'"
        )
    ) == 1


def test_calendar_correction_removes_existing_bar_from_trusted_reads(db):
    add_raw(db)
    service = HithinkDumpCollectionService()
    assert service.build_market_daily(db, batch_no="retry") == 1
    db.execute(
        text(
            "UPDATE market.trading_calendar SET is_open=false "
            "WHERE exchange='SH' AND trade_date='2026-09-21'"
        )
    )
    db.commit()

    assert service.build_market_daily(db, batch_no="retry") == 0
    assert db.scalar(text("SELECT quality_status FROM market.daily_bar")) == "rejected"
    assert db.scalar(
        text(
            "SELECT count(*) FROM metadata.data_quality_issue "
            "WHERE issue_type='CALENDAR_MISMATCH'"
        )
    ) == 1
    assert DailyBarRepository().get_latest_consumable(
        db, symbol="600000.SH", adjust_type="none"
    ) is None


def test_latest_invalid_revision_removes_existing_bar_from_trusted_reads(db):
    add_raw(db)
    service = HithinkDumpCollectionService()
    assert service.build_market_daily(db, batch_no="retry") == 1

    add_raw(db, close="13", high="12")

    assert service.build_market_daily(db, batch_no="retry") == 0
    assert db.scalar(text("SELECT quality_status FROM market.daily_bar")) == "rejected"
    assert (
        DailyBarRepository().get_latest_consumable(db, symbol="600000.SH", adjust_type="none")
        is None
    )


@pytest.mark.parametrize(
    ("volume", "amount", "issue_types"),
    [
        (None, "1000", ("VOLUME_MISSING",)),
        ("100", None, ("AMOUNT_MISSING",)),
        (None, None, ("VOLUME_MISSING", "AMOUNT_MISSING")),
        ("0", "1000", ("TURNOVER_INCONSISTENT",)),
        ("100", "0", ("TURNOVER_INCONSISTENT",)),
    ],
)
def test_fast_path_reuses_shared_warning_quality_rules(db, volume, amount, issue_types):
    add_raw(db, volume=volume, amount=amount)

    assert HithinkDumpCollectionService().build_market_daily(db, batch_no="retry") == 0
    assert db.execute(text("SELECT quality_status, is_suspect FROM market.daily_bar")).one() == (
        "suspect",
        True,
    )
    for issue_type in issue_types:
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM metadata.data_quality_issue WHERE issue_type=:issue_type"
                ),
                {"issue_type": issue_type},
            )
            == 1
        )
    HithinkDumpCollectionService().build_market_daily(db, batch_no="retry")
    for issue_type in issue_types:
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM metadata.data_quality_issue WHERE issue_type=:issue_type"
                ),
                {"issue_type": issue_type},
            )
            == 1
        )


def test_closed_quality_issue_does_not_hide_same_raw_failing_recheck(db):
    add_raw(db, volume=None)
    service = HithinkDumpCollectionService()
    service.build_market_daily(db, batch_no="retry")
    db.execute(
        text(
            "UPDATE metadata.data_quality_issue SET status='fixed' "
            "WHERE issue_type='VOLUME_MISSING'"
        )
    )
    db.commit()

    service.build_market_daily(db, batch_no="retry")

    assert (
        db.scalar(
            text(
                "SELECT count(*) FROM metadata.data_quality_issue WHERE issue_type='VOLUME_MISSING'"
            )
        )
        == 2
    )
    assert (
        db.scalar(
            text(
                "SELECT count(*) FROM metadata.data_quality_issue "
                "WHERE issue_type='VOLUME_MISSING' AND status='open'"
            )
        )
        == 1
    )


def test_fast_path_rechecks_historical_price_continuity_warnings(db):
    add_raw(db, close="15", high="16", pre_close="10", change_amount="0")

    assert HithinkDumpCollectionService().build_market_daily(db, batch_no="retry") == 0
    assert db.scalar(text("SELECT quality_status FROM market.daily_bar")) == "suspect"
    assert set(
        db.scalars(
            text(
                "SELECT issue_type FROM metadata.data_quality_issue "
                "WHERE issue_type IN ('PRICE_CONTINUITY_WARNING', 'PRICE_MOVE_EXTREME')"
            )
        ).all()
    ) == {"PRICE_CONTINUITY_WARNING", "PRICE_MOVE_EXTREME"}


def test_old_batch_without_window_cannot_roll_back_newer_selected_raw(db):
    old = add_raw(db)
    newest = add_raw(db, close="11")
    service = HithinkDumpCollectionService()
    service.build_market_daily(db, batch_no="retry")
    db.add(
        ImportBatch(
            id=999,
            batch_no="legacy",
            source_name="hithink_finance_dump",
            source_type="vendor",
            dataset="daily_bar",
            status="success",
            request_params={},
        )
    )
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
            if session.scalar(select(Security.id).where(Security.symbol == "600000.SH")) is None:
                session.add(
                    Security(
                        symbol="600000.SH",
                        name="浦发银行",
                        security_type="stock",
                        exchange="SH",
                        list_date=date(1999, 11, 10),
                        is_active=True,
                    )
                )
            for exchange in ("SH", "SZ", "BJ"):
                for day in (20, 21, 22):
                    existing = session.scalar(
                        select(TradingCalendar.id).where(
                            TradingCalendar.exchange == exchange,
                            TradingCalendar.trade_date == date(2026, 9, day),
                        )
                    )
                    if existing is None:
                        session.add(
                            TradingCalendar(
                                exchange=exchange,
                                trade_date=date(2026, 9, day),
                                is_open=day != 20,
                            )
                        )
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
    jobs = db.scalars(
        select(JobLog).where(JobLog.job_name == AUTO_DATA_SYNC_JOB).order_by(JobLog.id)
    ).all()
    assert len(jobs) == 3
    assert all(job.status == "success" for job in jobs)
    assert [job.metadata_["mode"] for job in jobs] == ["full_then_recent", "recent", "recent"]
    assert all(job.metadata_["end_date"] == "2026-09-22" for job in jobs)
    assert jobs[-1].rows_inserted == 0
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 22)
