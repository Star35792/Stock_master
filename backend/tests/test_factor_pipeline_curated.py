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
    seen_kwargs: list[dict] = []

    def __init__(self, daily_bar_repository: object | None = None) -> None:
        SpyService.seen.append(daily_bar_repository)

    def run(self, db, **kwargs) -> SpyServiceResult:
        SpyService.seen_kwargs.append(kwargs)
        return SpyServiceResult()


def test_daily_pipeline_selects_available_price_volume_extensions(monkeypatch) -> None:
    SpyService.seen_kwargs = []
    monkeypatch.setattr(pipeline, "BasicPriceVolumeFactorService", SpyService)
    monkeypatch.setattr(pipeline, "_FACTOR_SERVICES", [(SpyService, True)])
    pipeline.run_all_factors(
        start_date=__import__("datetime").date(2024, 1, 1),
        end_date=__import__("datetime").date(2024, 1, 31),
        session_factory=lambda: FakeSession(),
        job_log_repository=FakeJobLogRepository(),
    )
    selected = SpyService.seen_kwargs[0]["factor_names"]
    assert len(selected) == len(set(selected)) == 16
    assert set(selected) == set(pipeline.DAILY_PRICE_VOLUME_FACTORS)
    assert {
        "ret_1d", "momentum_5", "abnormal_volume_pressure",
        "reversal_5", "beta_60", "residual_volatility_60",
    } <= set(selected)
    assert "turnover_percentile_120" not in selected
    assert "turnover_rate" not in selected


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


def test_curated_reader_can_be_bound_to_certification_staging(monkeypatch, tmp_path) -> None:
    SpyService.seen = []
    monkeypatch.setattr(pipeline, "_FACTOR_SERVICES", [(SpyService, True)])
    monkeypatch.setattr(pipeline.settings, "factor_reads_curated", True)
    pipeline.run_all_factors(
        start_date=__import__("datetime").date(2024, 1, 1),
        end_date=__import__("datetime").date(2024, 1, 31),
        adjust_type="qfq",
        curated_path=tmp_path / ".prices_qfq.tmp-test",
        session_factory=lambda: FakeSession(),
        job_log_repository=FakeJobLogRepository(),
    )

    assert SpyService.seen[0].dataset_path == tmp_path / ".prices_qfq.tmp-test"


def test_orm_path_when_flag_disabled(monkeypatch) -> None:
    seen = _run(monkeypatch, flag=False, adjust_type="qfq")
    assert seen == [None]


def test_orm_path_for_non_qfq_even_when_enabled(monkeypatch) -> None:
    seen = _run(monkeypatch, flag=True, adjust_type="hfq")
    assert seen == [None]


def test_zero_row_service_is_reported_as_empty_and_pipeline_is_partial(monkeypatch) -> None:
    SpyService.seen = []
    job_log = FakeJobLogRepository()
    monkeypatch.setattr(pipeline, "_FACTOR_SERVICES", [(SpyService, True)])
    result = pipeline.run_all_factors(
        start_date=__import__("datetime").date(2024, 1, 1),
        end_date=__import__("datetime").date(2024, 1, 31),
        adjust_type="none",
        session_factory=lambda: FakeSession(),
        job_log_repository=job_log,
    )

    assert result.empty == ["SpyService"]
    assert result.failed == []
