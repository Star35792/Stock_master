"""Verify run_all_factors injects the curated reader only when enabled + qfq."""

import app.scheduler.factor_pipeline as pipeline
from app.lake import CuratedDailyBarReader


class FakeJobLog:
    def __init__(self) -> None:
        self.id = 1
        self.status = "running"
        self.rows_read = 0
        self.rows_inserted = 0
        self.rows_updated = 0
        self.rows_failed = 0
        self.error_message: str | None = None
        self.metadata_: dict | None = None


class FakeJobLogRepository:
    def create(self, db, *, job_name, job_type, batch_id=None, metadata=None):
        job = FakeJobLog()
        job.metadata_ = metadata
        return job

    def finish(self, db, *, job_log, status, **kwargs):
        job_log.status = status
        return job_log


class FakeSession:
    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class SpyServiceResult:
    total_rows = 0
    input_rows = 0
    run_ids: dict = {}


class SpyService:
    """Records the daily_bar_repository it was constructed with."""

    seen: list[object] = []

    def __init__(self, daily_bar_repository: object | None = None) -> None:
        SpyService.seen.append(daily_bar_repository)

    def run(self, db, **kwargs) -> SpyServiceResult:
        return SpyServiceResult()


def _run(monkeypatch, *, flag: bool, adjust_type: str) -> list[object]:
    SpyService.seen = []
    monkeypatch.setattr(pipeline, "_FACTOR_SERVICES", [(SpyService, True)])
    monkeypatch.setattr(pipeline.settings, "factor_reads_curated", flag)
    pipeline.run_all_factors(
        start_date=__import__("datetime").date(2024, 1, 1),
        end_date=__import__("datetime").date(2024, 1, 31),
        adjust_type=adjust_type,
        session_factory=lambda: FakeSession(),
        job_log_repository=FakeJobLogRepository(),
    )
    return SpyService.seen


def test_curated_reader_injected_when_enabled_and_qfq(monkeypatch) -> None:
    seen = _run(monkeypatch, flag=True, adjust_type="qfq")
    assert len(seen) == 1
    assert isinstance(seen[0], CuratedDailyBarReader)


def test_orm_path_when_flag_disabled(monkeypatch) -> None:
    seen = _run(monkeypatch, flag=False, adjust_type="qfq")
    assert seen == [None]


def test_orm_path_for_non_qfq_even_when_enabled(monkeypatch) -> None:
    seen = _run(monkeypatch, flag=True, adjust_type="hfq")
    assert seen == [None]
