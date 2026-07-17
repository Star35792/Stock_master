import pytest

from app.scheduler import (
    create_daily_sync_scheduler,
    create_recent_data_completion_scheduler,
    run_hithink_daily_sync,
    run_recent_data_completion,
)
from app.services import (
    DailyBarCollectionResult,
    GapBackfillResult,
    HithinkDailySyncResult,
    MarketBuildResult,
    RecentDataCompletionResult,
)


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True


class FakeSyncService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def sync(self, db: object, **kwargs: object) -> HithinkDailySyncResult:
        self.calls.append(kwargs)
        return HithinkDailySyncResult(
            reference_data=None,
            collection=DailyBarCollectionResult(
                batch_no="batch-1",
                source_name="hithink_finance",
                status="success",
                row_count=1,
                accepted_count=1,
                rejected_count=0,
                error_message=None,
            ),
            market_build=MarketBuildResult(
                candidates_count=1,
                trusted_count=1,
                rejected_count=0,
                issue_count=0,
            ),
        )


class FakeCompletionService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, db: object, **kwargs: object) -> RecentDataCompletionResult:
        self.calls.append(kwargs)
        return RecentDataCompletionResult(
            primary_sync=HithinkDailySyncResult(
                reference_data=None,
                collection=DailyBarCollectionResult(
                    batch_no="primary-1",
                    source_name="hithink_finance",
                    status="success",
                    row_count=1,
                    accepted_count=1,
                    rejected_count=0,
                    error_message=None,
                ),
                market_build=MarketBuildResult(
                    candidates_count=1,
                    trusted_count=1,
                    rejected_count=0,
                    issue_count=0,
                ),
            ),
            gap_backfill=GapBackfillResult(
                batch_no="gap-1",
                expected_rows=1,
                fetched_rows=1,
                inserted_rows=1,
                skipped_rows=0,
            ),
            gap_market_build=MarketBuildResult(
                candidates_count=1,
                trusted_count=1,
                rejected_count=0,
                issue_count=0,
            ),
        )


def test_scheduled_job_runs_sync_and_closes_session() -> None:
    session = FakeSession()
    service = FakeSyncService()

    result = run_hithink_daily_sync(
        symbols=["600519.SH"],
        days=7,
        service=service,  # type: ignore[arg-type]
        session_factory=lambda: session,  # type: ignore[arg-type]
    )

    assert service.calls == [
        {
            "symbols": ["600519.SH"],
            "days": 7,
            "adjust_type": "none",
            "refresh_reference_data": False,
        }
    ]
    assert session.closed is True
    assert result.market_build.trusted_count == 1


def test_scheduler_registers_weekday_single_instance_job() -> None:
    scheduler = create_daily_sync_scheduler(
        symbols=["600519.sh", " 000001.SZ "],
        hour=18,
        minute=10,
        days=7,
    )

    job = scheduler.get_job("hithink_daily_sync")

    assert job is not None
    assert job.kwargs["symbols"] == ["600519.SH", "000001.SZ"]
    assert job.kwargs["days"] == 7
    assert job.max_instances == 1
    assert job.coalesce is True
    assert str(job.trigger) == "cron[day_of_week='mon-fri', hour='18', minute='10']"


def test_scheduler_requires_positive_days() -> None:
    with pytest.raises(ValueError, match="days"):
        create_daily_sync_scheduler(symbols=["600519.SH"], days=0)


def test_scheduled_recent_completion_runs_and_closes_session() -> None:
    session = FakeSession()
    service = FakeCompletionService()

    result = run_recent_data_completion(
        symbols=["600519.SH"],
        days=7,
        service=service,  # type: ignore[arg-type]
        session_factory=lambda: session,  # type: ignore[arg-type]
    )

    assert service.calls == [
        {
            "symbols": ["600519.SH"],
            "days": 7,
            "adjust_type": "qfq",
            "refresh_reference_data": False,
        }
    ]
    assert session.closed is True
    assert result.gap_backfill.inserted_rows == 1


def test_recent_completion_scheduler_registers_weekday_single_instance_job() -> None:
    scheduler = create_recent_data_completion_scheduler(
        symbols=["600519.sh", " 000001.SZ "],
        hour=18,
        minute=20,
        days=7,
    )

    job = scheduler.get_job("recent_data_completion")

    assert job is not None
    assert job.kwargs["symbols"] == ["600519.SH", "000001.SZ"]
    assert job.kwargs["days"] == 7
    assert job.kwargs["adjust_type"] == "qfq"
    assert job.max_instances == 1
    assert job.coalesce is True
    assert str(job.trigger) == "cron[day_of_week='mon-fri', hour='18', minute='20']"


def test_recent_completion_scheduler_requires_qfq() -> None:
    with pytest.raises(ValueError, match="qfq"):
        create_recent_data_completion_scheduler(
            symbols=["600519.SH"],
            adjust_type="none",
        )
