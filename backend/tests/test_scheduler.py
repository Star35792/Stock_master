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


class FakeJobLog:
    _id_counter = 0

    def __init__(self) -> None:
        FakeJobLog._id_counter += 1
        self.id = FakeJobLog._id_counter
        self.status = "running"
        self.rows_read = 0
        self.rows_inserted = 0
        self.rows_updated = 0
        self.rows_failed = 0
        self.metadata_: dict | None = None
        self.error_message: str | None = None


class FakeJobLogRepository:
    def __init__(self) -> None:
        self.last_job: FakeJobLog | None = None

    def create(
        self,
        db: object,
        *,
        job_name: str,
        job_type: str,
        batch_id: int | None = None,
        metadata: dict | None = None,
    ) -> FakeJobLog:
        job = FakeJobLog()
        job.metadata_ = metadata
        self.last_job = job
        return job

    def finish(
        self, db: object, *, job_log: FakeJobLog, status: str, **kwargs: object
    ) -> FakeJobLog:
        job_log.status = status
        if "error_message" in kwargs:
            job_log.error_message = kwargs["error_message"]
        for key in ("rows_read", "rows_inserted", "rows_updated", "rows_failed"):
            if key in kwargs:
                setattr(job_log, key, kwargs[key])
        if kwargs.get("metadata"):
            job_log.metadata_ = kwargs["metadata"]
        return job_log


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def commit(self) -> None:
        pass


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
    def __init__(
        self,
        *,
        unresolved_rows: int = 0,
        unresolved_actionable_rows: int = 0,
        gap_status: str = "success",
        status_conflict_rows: int = 0,
        gap_market_rejected: int = 0,
        gap_market_suspect: int = 0,
    ) -> None:
        self.calls: list[dict] = []
        self.unresolved_rows = unresolved_rows
        self.unresolved_actionable_rows = unresolved_actionable_rows
        self.gap_status = gap_status
        self.status_conflict_rows = status_conflict_rows
        self.gap_market_rejected = gap_market_rejected
        self.gap_market_suspect = gap_market_suspect

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
                unresolved_rows=self.unresolved_rows,
                unresolved_actionable_rows=self.unresolved_actionable_rows,
                status=self.gap_status,
                status_conflict_rows=self.status_conflict_rows,
            ),
            gap_market_build=MarketBuildResult(
                candidates_count=1,
                trusted_count=(
                    0 if self.gap_market_rejected or self.gap_market_suspect else 1
                ),
                rejected_count=self.gap_market_rejected,
                issue_count=self.gap_market_rejected + self.gap_market_suspect,
                suspect_count=self.gap_market_suspect,
            ),
        )


def test_scheduled_job_runs_sync_and_closes_session() -> None:
    session = FakeSession()
    service = FakeSyncService()
    job_log_repo = FakeJobLogRepository()

    result = run_hithink_daily_sync(
        symbols=["600519.SH"],
        days=7,
        service=service,  # type: ignore[arg-type]
        session_factory=lambda: session,  # type: ignore[arg-type]
        job_log_repository=job_log_repo,
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
    job_log_repo = FakeJobLogRepository()

    result = run_recent_data_completion(
        symbols=["600519.SH"],
        days=7,
        service=service,  # type: ignore[arg-type]
        session_factory=lambda: session,  # type: ignore[arg-type]
        job_log_repository=job_log_repo,
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


def test_scheduled_recent_completion_marks_unresolved_gaps_partial() -> None:
    session = FakeSession()
    service = FakeCompletionService(unresolved_rows=1, status_conflict_rows=1)
    job_log_repo = FakeJobLogRepository()

    run_recent_data_completion(
        symbols=["600519.SH"],
        days=7,
        service=service,  # type: ignore[arg-type]
        session_factory=lambda: session,  # type: ignore[arg-type]
        job_log_repository=job_log_repo,
    )

    assert job_log_repo.last_job is not None
    assert job_log_repo.last_job.status == "partial"
    assert job_log_repo.last_job.rows_failed == 0
    assert job_log_repo.last_job.metadata_["gap"]["status_conflict_rows"] == 1
    assert {
        "missing_rows",
        "partial_rows",
        "suspect_rows",
        "status_explained_rows",
        "lifecycle_unknown_rows",
        "status_conflict_rows",
    } <= job_log_repo.last_job.metadata_["gap"].keys()


def test_scheduled_recent_completion_preserves_gap_partial_status() -> None:
    session = FakeSession()
    service = FakeCompletionService(gap_status="partial")
    job_log_repo = FakeJobLogRepository()

    run_recent_data_completion(
        symbols=["600519.SH"],
        days=7,
        service=service,  # type: ignore[arg-type]
        session_factory=lambda: session,  # type: ignore[arg-type]
        job_log_repository=job_log_repo,
    )

    assert job_log_repo.last_job is not None
    assert job_log_repo.last_job.status == "partial"


@pytest.mark.parametrize(
    ("rejected_count", "suspect_count"),
    [(1, 0), (0, 1)],
)
def test_scheduled_recent_completion_marks_untrusted_gap_build_partial(
    rejected_count: int,
    suspect_count: int,
) -> None:
    session = FakeSession()
    service = FakeCompletionService(
        gap_market_rejected=rejected_count,
        gap_market_suspect=suspect_count,
    )
    job_log_repo = FakeJobLogRepository()

    run_recent_data_completion(
        symbols=["600519.SH"],
        days=7,
        service=service,  # type: ignore[arg-type]
        session_factory=lambda: session,  # type: ignore[arg-type]
        job_log_repository=job_log_repo,
    )

    assert job_log_repo.last_job is not None
    assert job_log_repo.last_job.status == "partial"
    assert job_log_repo.last_job.rows_failed == 1
    assert job_log_repo.last_job.metadata_["gap"]["rejected_count"] == rejected_count
    assert (
        job_log_repo.last_job.metadata_["gap"]["market_suspect_count"] == suspect_count
    )


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
