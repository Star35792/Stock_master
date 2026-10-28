import asyncio
import importlib
import threading
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.scheduler import auto_data_sync as scheduler


def test_published_closure_requires_current_factor_set() -> None:
    target_date = date(2026, 10, 26)
    metadata = {"target_trade_date": target_date.isoformat(), "publish_eligible": True}

    class Session:
        def scalars(self, statement):
            return SimpleNamespace(
                all=lambda: [SimpleNamespace(status="partial", metadata_=metadata)]
            )

    assert scheduler._has_published_closure(Session(), target_date=target_date) is False
    metadata["daily_price_volume_factors"] = list(scheduler.DAILY_PRICE_VOLUME_FACTORS)
    assert scheduler._has_published_closure(Session(), target_date=target_date) is True


def config(**overrides):
    values = {
        "auto_data_sync_enabled": True,
        "hithink_finance_api_key": "test-key",
        "auto_data_sync_interval_seconds": 0.01,
    }
    return SimpleNamespace(**(values | overrides))


@pytest.mark.parametrize(
    "overrides", [{"auto_data_sync_enabled": False}, {"hithink_finance_api_key": None}]
)
def test_disabled_or_unconfigured_worker_does_not_run(monkeypatch, overrides):
    calls = []
    monkeypatch.setattr(scheduler, "run_auto_data_sync", lambda: calls.append(True))

    async def scenario():
        worker = scheduler.AutoDataSyncWorker(config(**overrides))
        worker.start()
        await asyncio.sleep(0)
        await worker.stop()

    asyncio.run(scenario())
    assert calls == []


def test_start_is_nonblocking_and_shutdown_waits_for_inflight_sync(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def sync():
        entered.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(scheduler, "run_auto_data_sync", sync)

    async def scenario():
        worker = scheduler.AutoDataSyncWorker(config())
        worker.start()
        worker.start()  # lifespan reuse cannot create a second loop
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            # The event loop remains responsive while the network thread blocks.
            stop = asyncio.create_task(worker.stop())
            await asyncio.sleep(0)
            assert not stop.done()
        finally:
            release.set()
        await asyncio.wait_for(stop, timeout=2)
        assert worker._task.done()

    asyncio.run(scenario())


def test_failure_retries_and_periodic_calls_never_overlap(monkeypatch):
    calls = 0
    active = 0
    finished = threading.Event()

    def sync():
        nonlocal calls, active
        active += 1
        assert active == 1
        calls += 1
        try:
            if calls == 1:
                raise RuntimeError("vendor unavailable")
            finished.set()
        finally:
            active -= 1

    monkeypatch.setattr(scheduler, "run_auto_data_sync", sync)

    async def scenario():
        worker = scheduler.AutoDataSyncWorker(config())
        worker.start()
        try:
            assert await asyncio.to_thread(finished.wait, 2)
        finally:
            await worker.stop()
        count_at_shutdown = calls
        await asyncio.sleep(0.03)
        assert calls == count_at_shutdown

    asyncio.run(scenario())
    assert calls >= 2


def test_process_lock_skips_overlapping_runs(monkeypatch):
    def unexpected_session():
        pytest.fail("overlapping task opened a database session")

    monkeypatch.setattr(scheduler, "SessionLocal", unexpected_session)
    assert scheduler._sync_lock.acquire(blocking=False)
    try:
        scheduler.run_auto_data_sync()
    finally:
        scheduler._sync_lock.release()


def test_advanced_dump_triggers_data_to_factor_closure(monkeypatch):
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    sync_result = SimpleNamespace(advanced=True, end_date=date(2026, 10, 26))
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(
        scheduler,
        "AutoDataSyncService",
        lambda: SimpleNamespace(sync=lambda db: sync_result),
    )
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_daily_loop",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(failed=False),
    )

    scheduler.run_auto_data_sync()

    assert len(calls) == 1
    assert calls[0]["skip_sync"] is True
    assert calls[0]["target_date"] == date(2026, 10, 26)
    assert calls[0]["adjustment_version"] == "v1"
    assert calls[0]["code_version"].startswith("source-")


def test_unpublished_closure_is_retried_without_new_market_date(monkeypatch):
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def scalars(self, statement):
            return SimpleNamespace(all=lambda: [])

    sync_result = SimpleNamespace(advanced=False, end_date=date(2026, 10, 26))
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(
        scheduler,
        "AutoDataSyncService",
        lambda: SimpleNamespace(sync=lambda db: sync_result),
    )
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_daily_loop",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(failed=False),
    )

    scheduler.run_auto_data_sync()

    assert len(calls) == 1
    assert calls[0]["target_date"] == date(2026, 10, 26)


def test_lifespan_initializes_then_starts_and_stops_worker(monkeypatch):
    main = importlib.import_module("app.main")
    events = []

    class Worker:
        def __init__(self, settings):
            pass

        def start(self):
            events.append("start")

        async def stop(self):
            events.append("stop")

    monkeypatch.setattr(main, "initialize_database", lambda: events.append("database"))
    monkeypatch.setattr(main, "AutoDataSyncWorker", Worker)

    async def scenario():
        async with main.lifespan(main.create_app()):
            assert events == ["database", "start"]
        assert events == ["database", "start", "stop"]

    asyncio.run(scenario())


def test_health_is_available_while_startup_sync_is_waiting(monkeypatch):
    main = importlib.import_module("app.main")
    entered = threading.Event()
    release = threading.Event()

    def sync():
        entered.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(main.settings, "auto_data_sync_enabled", True)
    monkeypatch.setattr(main.settings, "hithink_finance_api_key", "test-key")
    monkeypatch.setattr(main, "initialize_database", lambda: None)
    monkeypatch.setattr(scheduler, "run_auto_data_sync", sync)
    with TestClient(main.create_app()) as client:
        try:
            assert entered.wait(timeout=2)
            assert client.get("/health").status_code == 200
        finally:
            release.set()
