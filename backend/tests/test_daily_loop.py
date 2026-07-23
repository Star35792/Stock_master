from datetime import date

import pytest

from app.config.settings import Settings
from app.scheduler.daily_loop import run_daily_loop
from app.scheduler.factor_pipeline import FactorBatchResult
from app.services import (
    DailyBarCollectionResult,
    HithinkDailySyncResult,
    MarketBuildResult,
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
        self.jobs: list[FakeJobLog] = []

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
        self.jobs.append(job)
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


class FakeSecurityRepository:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols

    def get_active_symbols(self, db: object) -> list[str]:
        return self.symbols


def _fake_sync_result(trusted_count: int = 3) -> HithinkDailySyncResult:
    return HithinkDailySyncResult(
        reference_data=None,
        collection=DailyBarCollectionResult(
            batch_no="batch-1",
            source_name="hithink_finance",
            status="success",
            row_count=5,
            accepted_count=5,
            rejected_count=0,
            error_message=None,
        ),
        market_build=MarketBuildResult(
            candidates_count=5,
            trusted_count=trusted_count,
            rejected_count=0,
            issue_count=0,
        ),
    )


def _fake_factor_result(failed: list[str] | None = None) -> FactorBatchResult:
    result = FactorBatchResult(
        start_date=date.today(),
        end_date=date.today(),
        adjust_type="qfq",
    )
    if failed:
        for name in failed:
            result.services[name] = {"status": "failed", "error": "nope", "total_rows": 0}
    else:
        result.services["BasicPriceVolumeFactorService"] = {
            "status": "success",
            "total_rows": 10,
            "input_rows": 5,
            "run_ids": [1],
        }
    return result


def test_daily_loop_runs_sync_then_factor_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["600519.SH", "000001.SZ"])

    sync_calls: list[dict] = []
    factor_calls: list[dict] = []

    def fake_sync(*, symbols, **kwargs: object) -> HithinkDailySyncResult:
        sync_calls.append({"symbols": symbols, **kwargs})
        return _fake_sync_result(trusted_count=3)

    def fake_factors(*, symbols, **kwargs: object) -> FactorBatchResult:
        factor_calls.append({"symbols": symbols, **kwargs})
        return _fake_factor_result()

    monkeypatch.setattr("app.scheduler.daily_loop.run_hithink_daily_sync", fake_sync)
    monkeypatch.setattr("app.scheduler.daily_loop.run_all_factors", fake_factors)

    result = run_daily_loop(
        symbols=["600519.SH", "000001.SZ"],
        sync_days=7,
        factor_lookback_days=5,
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is False
    assert result.sync_result is not None
    assert result.factor_result is not None
    assert result.total_rows == 13  # 3 sync + 10 factor
    assert len(sync_calls) == 1
    assert sync_calls[0]["symbols"] == ["600519.SH", "000001.SZ"]
    assert sync_calls[0]["days"] == 7
    assert sync_calls[0]["adjust_type"] == "none"
    assert sync_calls[0]["refresh_reference_data"] is False
    assert sync_calls[0]["session_factory"] is not None
    assert sync_calls[0]["job_log_repository"] is job_log_repo
    assert len(factor_calls) == 1
    assert factor_calls[0]["symbols"] == ["600519.SH", "000001.SZ"]
    assert factor_calls[0]["adjust_type"] == "qfq"
    assert len(job_log_repo.jobs) == 1
    assert job_log_repo.jobs[0].status == "success"
    assert job_log_repo.jobs[0].metadata_["sync_batch_no"] == "batch-1"
    assert session.closed is True


def test_daily_loop_fetches_active_symbols_when_none_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["000001.SZ"])
    sync_calls: list[dict] = []

    def fake_sync(*, symbols, **kwargs: object) -> HithinkDailySyncResult:
        sync_calls.append({"symbols": symbols})
        return _fake_sync_result()

    monkeypatch.setattr("app.scheduler.daily_loop.run_hithink_daily_sync", fake_sync)
    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_all_factors", lambda **kwargs: _fake_factor_result()
    )

    result = run_daily_loop(
        symbols=None,
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is False
    assert sync_calls[0]["symbols"] == ["000001.SZ"]


def test_daily_loop_marks_failed_when_sync_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["600519.SH"])

    def fake_sync(*, symbols: object = None, **kwargs: object) -> HithinkDailySyncResult:
        raise RuntimeError("sync broken")

    monkeypatch.setattr("app.scheduler.daily_loop.run_hithink_daily_sync", fake_sync)

    result = run_daily_loop(
        symbols=["600519.SH"],
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is True
    assert result.sync_error is not None
    assert "sync broken" in result.sync_error
    assert result.factor_result is None
    assert job_log_repo.jobs[0].status == "failed"
    assert "sync broken" in job_log_repo.jobs[0].error_message


def test_daily_loop_marks_partial_when_factor_service_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["600519.SH"])

    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_hithink_daily_sync",
        lambda **kwargs: _fake_sync_result(),
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_all_factors",
        lambda **kwargs: _fake_factor_result(failed=["SomeService"]),
    )

    result = run_daily_loop(
        symbols=["600519.SH"],
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is False
    assert result.factor_result is not None
    assert result.factor_result.failed == ["SomeService"]
    assert job_log_repo.jobs[0].status == "partial"
    assert "SomeService" in job_log_repo.jobs[0].error_message


def test_daily_loop_skips_curated_build_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["600519.SH"])

    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_hithink_daily_sync",
        lambda **kwargs: _fake_sync_result(),
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_all_factors",
        lambda **kwargs: _fake_factor_result(),
    )

    result = run_daily_loop(
        symbols=["600519.SH"],
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is False
    assert result.curated_result is None
    assert result.curated_error is None
    assert job_log_repo.jobs[0].metadata_.get("curated_rebuilt") is None


def test_daily_loop_fails_when_curated_flag_requires_duckdb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["600519.SH"])

    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_hithink_daily_sync",
        lambda **kwargs: _fake_sync_result(),
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_all_factors",
        lambda **kwargs: _fake_factor_result(),
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop.settings",
        Settings(factor_reads_curated=True),
    )

    result = run_daily_loop(
        symbols=["600519.SH"],
        factor_adjust_type="qfq",
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is True
    assert result.curated_error is not None
    assert "DuckDB" in result.curated_error
    assert result.factor_result is None
    assert job_log_repo.jobs[0].status == "failed"
