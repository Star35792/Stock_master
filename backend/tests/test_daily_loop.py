from datetime import date
from types import SimpleNamespace

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

    def get(self, db: object, job_id: int) -> FakeJobLog | None:
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

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
        self.executed: list[tuple[object, ...]] = []

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def execute(self, statement: object, params: object | None = None) -> object:
        self.executed.append((statement, params))
        return self

    def scalar(self, statement: object | None = None) -> object | None:
        raise RuntimeError("scalar not supported on FakeSession")

    def scalar_one_or_none(self) -> None:
        return None

    def fetchone(self) -> tuple | None:
        raise RuntimeError("fetchone not supported on FakeSession")


@pytest.fixture(autouse=True)
def _reset_daily_loop_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep daily_loop tests independent of local .env overrides (e.g.
    # FACTOR_READS_CURATED=true). Tests that explicitly enable curated mode
    # monkeypatch settings again after this fixture runs.
    monkeypatch.setattr(
        "app.scheduler.daily_loop.settings",
        Settings(factor_reads_curated=False),
    )
    # Mock trading calendar so all existing tests see today as a trading day.
    day = date.today()
    monkeypatch.setattr(
        "app.scheduler.daily_loop.completed_daily_cutoff",
        lambda: day,
    )

    def fake_get_open_dates(self, db, *, exchange, dates):
        return {day}

    monkeypatch.setattr(
        "app.repositories.market.TradingCalendarRepository.get_open_dates",
        fake_get_open_dates,
    )
    monkeypatch.setattr(
        "app.repositories.market.TradingCalendarRepository.get_many_on_date",
        lambda self, db, exchanges, trade_date: [SimpleNamespace(is_open=True)],
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop._gather_certification_metadata",
        lambda db, target_date, **kwargs: {
            "target_trade_date": target_date.isoformat(),
            "gate_status": "pending",
            "publish_eligible": False,
            "reason_codes": [],
            "gate_market_date_missing": False,
            "gate_duplicate_keys": False,
            "gate_qfq_coverage_anomaly": False,
            "gate_core_factor_partial": False,
            "gate_query_error": False,
        },
    )


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


def test_daily_loop_can_close_a_pre_synced_full_market_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    monkeypatch.setattr(
        "app.scheduler.daily_loop.AutoDataSyncRepository.market_coverage_on_date",
        lambda self, db, trade_date: {
            "trade_date": trade_date.isoformat(),
            "eligible_stocks": 100,
            "covered_stocks": 99,
            "stock_coverage_rate": 0.99,
            "by_exchange": {},
            "index_rows": 0,
        },
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_hithink_daily_sync",
        lambda **kwargs: pytest.fail("per-symbol sync must stay bypassed"),
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop.run_all_factors",
        lambda **kwargs: _fake_factor_result(),
    )

    result = run_daily_loop(
        symbols=["600519.SH"],
        skip_sync=True,
        target_date=date.today(),
        session_factory=lambda: session,
        job_log_repository=job_log_repo,
    )

    assert result.failed is False
    assert result.sync_result is None
    assert job_log_repo.jobs[0].metadata_["sync_skipped"] is True


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


def test_daily_loop_marks_failed_when_factor_service_fails(
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

    assert result.failed is True
    assert result.factor_result is not None
    assert result.factor_result.failed == ["SomeService"]
    assert job_log_repo.jobs[0].status == "failed"
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


def test_daily_loop_rebuilds_curated_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["600519.SH"])
    rebuild_calls: list[dict] = []
    curated_calls: list[str] = []

    def fake_rebuild(self: object, db: object, **kwargs: object):
        rebuild_calls.append(kwargs)
        from app.services.adjustment_build import AdjustmentBuildResult

        return AdjustmentBuildResult(factor_rows=10, qfq_bar_rows=20, version="local")

    def fake_build_prices_qfq_from_db(
        self: object, db_path: str, **kwargs: object
    ) -> object:
        curated_calls.append((db_path, kwargs))
        from app.lake.curated import CuratedDatasetResult

        return CuratedDatasetResult(
            dataset="prices_qfq",
            path="/tmp/prices_qfq",
            row_count=20,
            min_date="2026-01-01",
            max_date="2026-01-10",
            created_at="2026-01-10T00:00:00+00:00",
        )

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
    monkeypatch.setattr(
        "app.services.adjustment_build.AdjustmentBuildService.rebuild_qfq",
        fake_rebuild,
    )
    monkeypatch.setattr(
        "app.lake.curated.CuratedLayer.build_prices_qfq_from_db",
        fake_build_prices_qfq_from_db,
    )

    monkeypatch.setattr(
        "app.scheduler.daily_loop.ReleaseService.compute_parquet_checksum",
        lambda path: "c" * 64,
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop.ReleaseService.build_parquet_manifest",
        lambda path: [],
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop._compute_market_qfq_checksum",
        lambda db, **kwargs: "m" * 64,
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop.CuratedLayer.publish_prices_qfq",
        lambda self, staged: staged,
    )
    monkeypatch.setattr(
        "app.scheduler.daily_loop._market_qfq_result",
        lambda db: SimpleNamespace(
            row_count=20,
            min_date=date(2026, 1, 1),
            max_date=date(2026, 1, 10),
        ),
    )
    release_ids = iter(["market-release", "curated-release"])
    monkeypatch.setattr(
        "app.scheduler.daily_loop.ReleaseService.build_release",
        lambda self, db, **kwargs: SimpleNamespace(release_id=next(release_ids)),
    )
    activated: list[str] = []
    monkeypatch.setattr(
        "app.scheduler.daily_loop.ReleaseRepository.activate",
        lambda self, db, *, release_id: activated.append(release_id),
    )

    result = run_daily_loop(
        symbols=["600519.SH"],
        factor_adjust_type="qfq",
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is False
    assert result.curated_result is not None
    assert result.curated_result.row_count == 20
    assert len(rebuild_calls) == 1
    assert rebuild_calls[0].get("source_name") == "hithink_finance_dump"
    assert len(curated_calls) == 1
    assert curated_calls[0][0].endswith(".duckdb")
    assert curated_calls[0][1].get("factor_source_name") == "hithink_finance_dump"
    assert curated_calls[0][1].get("factor_version") == "v1"
    assert curated_calls[0][1].get("publish") is False
    assert job_log_repo.jobs[0].metadata_["curated_rebuilt"] is True
    assert job_log_repo.jobs[0].metadata_["adjustment_factor_rows"] == 10


def test_daily_loop_fails_when_sync_adjust_type_incompatible_with_curated(
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
        "app.scheduler.daily_loop.settings",
        Settings(factor_reads_curated=True),
    )

    result = run_daily_loop(
        symbols=["600519.SH"],
        sync_adjust_type="qfq",
        factor_adjust_type="qfq",
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is True
    assert result.curated_error is not None
    assert "sync_adjust_type='none'" in result.curated_error
    assert result.factor_result is None
    assert job_log_repo.jobs[0].status == "failed"


def test_daily_loop_skips_on_non_trading_day(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["600519.SH"])

    def fake_get_many_on_date(self, db, *, exchanges, trade_date):
        return [SimpleNamespace(is_open=False)]

    monkeypatch.setattr(
        "app.repositories.market.TradingCalendarRepository.get_many_on_date",
        fake_get_many_on_date,
    )

    sync_calls: list[dict] = []

    def fake_sync(**kwargs: object) -> object:
        sync_calls.append(kwargs)
        return _fake_sync_result()

    monkeypatch.setattr("app.scheduler.daily_loop.run_hithink_daily_sync", fake_sync)

    result = run_daily_loop(
        symbols=["600519.SH"],
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.skipped is True
    assert result.skip_reason == "MARKET_CLOSED"
    assert result.failed is False
    assert len(sync_calls) == 0
    assert len(job_log_repo.jobs) == 1
    assert job_log_repo.jobs[0].status == "success"
    assert job_log_repo.jobs[0].metadata_.get("gate_status") == "skipped"
    assert job_log_repo.jobs[0].metadata_.get("publish_eligible") is False
    assert "MARKET_CLOSED" in job_log_repo.jobs[0].metadata_.get("reason_codes", [])


def test_daily_loop_fails_when_calendar_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    job_log_repo = FakeJobLogRepository()
    security_repo = FakeSecurityRepository(["600519.SH"])

    def fake_get_many_on_date(self, db, *, exchanges, trade_date):
        return []

    monkeypatch.setattr(
        "app.repositories.market.TradingCalendarRepository.get_many_on_date",
        fake_get_many_on_date,
    )

    sync_calls: list[dict] = []

    def fake_sync(**kwargs: object) -> object:
        sync_calls.append(kwargs)
        return _fake_sync_result()

    monkeypatch.setattr("app.scheduler.daily_loop.run_hithink_daily_sync", fake_sync)

    result = run_daily_loop(
        symbols=["600519.SH"],
        session_factory=lambda: session,
        security_repository=security_repo,
        job_log_repository=job_log_repo,
    )

    assert result.failed is True
    assert result.skip_reason == "CALENDAR_MISSING"
    assert len(sync_calls) == 0
    assert len(job_log_repo.jobs) == 1
    assert job_log_repo.jobs[0].status == "failed"
    assert "CALENDAR_MISSING" in job_log_repo.jobs[0].metadata_.get("reason_codes", [])
