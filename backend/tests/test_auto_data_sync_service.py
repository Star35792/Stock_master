from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from app.data_sources.hithink_dump import HithinkDump, HithinkDumpKind
from app.database.base import Base
from app.database.session import SCHEMAS
from app.models.market import TradingCalendar
from app.models.system import JobLog
from app.repositories.system.auto_data_sync import AUTO_DATA_SYNC_JOB, AutoDataSyncRepository
from app.services.auto_data_sync import AutoDataSyncService, completed_daily_cutoff
from app.services.hithink_dump_collection import HithinkDumpCollectionResult


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"duckdb:///{(tmp_path / 'auto-sync.duckdb').as_posix()}")
    with engine.begin() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


class FakeSource:
    def __init__(
        self,
        *,
        recent=(date(2026, 9, 15), date(2026, 9, 22)),
        full=(date(2020, 1, 1), date(2026, 9, 14)),
    ):
        self.ranges = {HithinkDumpKind.RECENT_DAILY: recent, HithinkDumpKind.FULL_DAILY: full}
        self.downloads = []

    def download(self, kind):
        self.downloads.append(kind)
        return HithinkDump(kind, Path(f"{kind.value}.parquet"), "release-test")

    def daily_date_range(self, path):
        return self.ranges[HithinkDumpKind(path.stem)]


class FakeReference:
    def __init__(self):
        self.calls = 0

    def sync_hithink_reference_data(self, db):
        self.calls += 1
        # Include closed days explicitly: absence must not imply a holiday.
        if self.calls == 1:
            for exchange in ("SH", "SZ", "BJ", "HK"):
                db.add(
                    TradingCalendar(
                        exchange=exchange,
                        trade_date=date(2020, 1, 1),
                        is_open=False,
                    )
                )
            for offset in range(40):
                day = date(2026, 8, 25) + timedelta(days=offset)
                for exchange in ("SH", "SZ", "BJ", "HK"):
                    db.add(
                        TradingCalendar(
                            exchange=exchange, trade_date=day, is_open=day.weekday() < 5
                        )
                    )
            db.commit()


class FakeCollection:
    def __init__(self, source=None):
        self.data_source = source or FakeSource()
        self.collected = []
        self.built = []
        self.fail_kind = None

    def collect(self, db, *, full, dump):
        assert full == (dump.kind == HithinkDumpKind.FULL_DAILY)
        if dump.kind == self.fail_kind:
            # Exercise recovery of an actually aborted DuckDB transaction.
            db.execute(text("SELECT * FROM definitely_missing_table"))
        self.collected.append(dump.kind)
        start, end = self.data_source.daily_date_range(dump.path)
        return HithinkDumpCollectionResult(
            f"batch-{len(self.collected)}",
            dump.release_tag,
            str(dump.path),
            start,
            end,
            10,
            3,
            7,
        )

    def build_market_daily(self, db, *, batch_no, end_date):
        self.built.append((batch_no, end_date))
        return 2


class CoverageRepository(AutoDataSyncRepository):
    def __init__(self, rate: float = 1.0):
        self.rate = rate

    def market_coverage_on_date(self, db, *, trade_date):
        eligible = 100
        covered = int(eligible * self.rate)
        return {
            "trade_date": trade_date.isoformat(),
            "eligible_stocks": eligible,
            "covered_stocks": covered,
            "stock_coverage_rate": self.rate,
            "by_exchange": {"SH": {"eligible": eligible, "covered": covered}},
            "index_rows": 0,
        }


def make_service(source=None, *, coverage_rate: float = 1.0):
    collection = FakeCollection(source)
    reference = FakeReference()
    service = AutoDataSyncService(
        collection=collection,
        reference=reference,
        repository=CoverageRepository(coverage_rate),
    )
    return service, collection, reference


def jobs(db):
    return list(
        db.scalars(
            select(JobLog)
            .where(
                JobLog.job_name == AUTO_DATA_SYNC_JOB,
            )
            .order_by(JobLog.id)
        )
    )


def seed_success(db, through):
    db.add(
        JobLog(
            job_name=AUTO_DATA_SYNC_JOB,
            job_type="import",
            status="success",
            metadata_={"end_date": through.isoformat()},
        )
    )
    db.commit()


def test_first_sync_imports_full_then_recent_with_auditable_watermark(db):
    service, collection, reference = make_service()
    result = service.sync(db, cutoff=date(2026, 9, 21))

    assert collection.collected == [HithinkDumpKind.FULL_DAILY, HithinkDumpKind.RECENT_DAILY]
    assert reference.calls == 1
    assert all(cutoff == date(2026, 9, 21) for _, cutoff in collection.built)
    job = jobs(db)[0]
    assert job.status == "success"
    assert (job.rows_read, job.rows_inserted) == (20, 6)
    assert job.metadata_["mode"] == "full_then_recent"
    assert job.metadata_["adjust_type"] == "none"
    assert job.metadata_["skipped_rows"] == 14
    assert job.metadata_["market_rows"] == 4
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 21)
    assert result.advanced is True
    assert result.coverage["stock_coverage_rate"] == 1.0


def test_incomplete_latest_cross_section_does_not_advance_watermark(db):
    seed_success(db, date(2026, 9, 21))
    service, _, _ = make_service(coverage_rate=0.90)

    with pytest.raises(ValueError, match="cross-section is incomplete"):
        service.sync(db, cutoff=date(2026, 9, 22))

    assert jobs(db)[-1].status == "failed"
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 21)


def test_completed_through_uses_started_at_not_rebuilt_sequence_id(db):
    db.add_all(
        [
            JobLog(
                id=900,
                job_name=AUTO_DATA_SYNC_JOB,
                job_type="import",
                status="success",
                started_at=datetime(2026, 9, 21, tzinfo=UTC),
                metadata_={"end_date": "2026-09-21"},
            ),
            JobLog(
                id=5,
                job_name=AUTO_DATA_SYNC_JOB,
                job_type="import",
                status="success",
                started_at=datetime(2026, 9, 22, tzinfo=UTC),
                metadata_={"end_date": "2026-09-22"},
            ),
        ]
    )
    db.commit()

    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 22)


def test_success_watermark_uses_recent_only_and_does_not_regress(db):
    seed_success(db, date(2026, 9, 21))
    service, collection, _ = make_service()
    service.sync(db, cutoff=date(2026, 9, 20))

    assert collection.collected == [HithinkDumpKind.RECENT_DAILY]
    assert jobs(db)[-1].metadata_["mode"] == "recent"
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 21)


def test_long_shutdown_reloads_full_history(db):
    seed_success(db, date(2026, 9, 1))
    service, collection, _ = make_service()
    service.sync(db, cutoff=date(2026, 9, 22))
    assert collection.collected == [HithinkDumpKind.FULL_DAILY, HithinkDumpKind.RECENT_DAILY]
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 22)


@pytest.mark.parametrize(
    "previous,full,error",
    [
        (None, (date(2020, 1, 1), date(2026, 9, 10)), "continuous coverage"),
        (date(2019, 12, 1), (date(2020, 1, 1), date(2026, 9, 14)), "older"),
    ],
)
def test_uncovered_dump_interval_is_rejected_without_advancing(db, previous, full, error):
    if previous:
        seed_success(db, previous)
    service, collection, _ = make_service(FakeSource(full=full))
    with pytest.raises(ValueError, match=error):
        service.sync(db, cutoff=date(2026, 9, 22))
    assert collection.collected == []
    assert jobs(db)[-1].status == "failed"
    assert AutoDataSyncRepository().completed_through(db) == previous


def test_failed_database_transaction_is_logged_and_retried_from_previous_watermark(db):
    seed_success(db, date(2026, 9, 1))
    service, collection, _ = make_service()
    collection.fail_kind = HithinkDumpKind.RECENT_DAILY
    with pytest.raises(Exception, match="definitely_missing_table"):
        service.sync(db, cutoff=date(2026, 9, 22))
    assert jobs(db)[-1].status == "failed"
    assert "definitely_missing_table" in jobs(db)[-1].error_message
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 1)

    collection.fail_kind = None
    service.sync(db, cutoff=date(2026, 9, 22))
    assert collection.collected == [
        HithinkDumpKind.FULL_DAILY,
        HithinkDumpKind.FULL_DAILY,
        HithinkDumpKind.RECENT_DAILY,
    ]
    assert jobs(db)[-1].status == "success"
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 22)


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (9, 59, date(2026, 9, 21)),
        (10, 0, date(2026, 9, 22)),
        (16, 0, date(2026, 9, 22)),
    ],
)
def test_completed_cutoff_uses_shanghai_eighteen_hour_boundary(hour, minute, expected):
    assert completed_daily_cutoff(datetime(2026, 9, 22, hour, minute, tzinfo=UTC)) == expected


def test_weekend_between_dump_windows_is_not_a_missing_trading_day(db):
    source = FakeSource(
        recent=(date(2026, 9, 21), date(2026, 9, 22)), full=(date(2020, 1, 1), date(2026, 9, 18))
    )
    service, collection, _ = make_service(source)
    service.sync(db, cutoff=date(2026, 9, 22))
    assert jobs(db)[-1].status == "success"
    assert len(collection.built) == 2


def test_weekend_since_previous_success_does_not_require_full_dump(db):
    seed_success(db, date(2026, 9, 18))
    service, collection, _ = make_service(
        FakeSource(
            recent=(date(2026, 9, 21), date(2026, 9, 22)),
        )
    )
    service.sync(db, cutoff=date(2026, 9, 22))
    assert collection.collected == [HithinkDumpKind.RECENT_DAILY]


def test_calendar_gaps_are_conservative_but_adjacent_dates_need_no_calendar(db):
    repository = AutoDataSyncRepository()
    assert repository.has_uncovered_sessions(
        db,
        after=date(2026, 9, 18),
        before=date(2026, 9, 21),
    )
    assert not repository.has_uncovered_sessions(
        db,
        after=date(2026, 9, 21),
        before=date(2026, 9, 22),
    )
    FakeReference().sync_hithink_reference_data(db)
    db.execute(delete(TradingCalendar).where(TradingCalendar.exchange == "BJ"))
    db.commit()
    assert repository.has_uncovered_sessions(
        db,
        after=date(2026, 9, 18),
        before=date(2026, 9, 21),
    )


def test_sparse_open_only_calendar_distinguishes_weekend_from_missed_session(db):
    FakeReference().sync_hithink_reference_data(db)
    db.execute(delete(TradingCalendar).where(TradingCalendar.is_open.is_(False)))
    db.commit()
    repository = AutoDataSyncRepository()
    assert not repository.has_uncovered_sessions(
        db,
        after=date(2026, 9, 18),
        before=date(2026, 9, 21),
    )
    assert repository.has_uncovered_sessions(
        db,
        after=date(2026, 9, 17),
        before=date(2026, 9, 21),
    )


@pytest.mark.parametrize("missing", ["all", "BJ", "stale", "start"])
def test_missing_calendar_coverage_fails_without_advancing_watermark(db, missing):
    seed_success(db, date(2026, 9, 21))
    service, collection, reference = make_service()
    reference.sync_hithink_reference_data(db)
    condition = {
        "all": True,
        "BJ": TradingCalendar.exchange == "BJ",
        "stale": TradingCalendar.trade_date >= date(2026, 9, 22),
        "start": TradingCalendar.trade_date < date(2026, 9, 16),
    }[missing]
    db.execute(delete(TradingCalendar).where(condition))
    db.commit()
    with pytest.raises(ValueError, match="calendar coverage"):
        service.sync(db, cutoff=date(2026, 9, 22))
    assert collection.collected == []
    assert collection.built == []
    assert jobs(db)[-1].status == "failed"
    assert AutoDataSyncRepository().completed_through(db) == date(2026, 9, 21)


def test_empty_scan_window_needs_no_calendar(db):
    AutoDataSyncRepository().require_calendar_coverage(
        db,
        start_date=date(2026, 9, 22),
        end_date=date(2026, 9, 21),
    )
