from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, date, datetime
from threading import Lock
from time import sleep
from types import SimpleNamespace

import pytest

from app.schemas.operations import OperationsActionPreview, OperationsActionStartRequest
from app.services import operations_actions
from app.services.operations_actions import OperationsActionConflictError, OperationsActionService


def test_concurrent_retries_share_one_audited_action(monkeypatch):
    created = []
    now = datetime.now(UTC)

    class Repository:
        def get_operator_request(self, db, *, idempotency_key):
            return next(
                (job for job in created if job.metadata_["idempotency_key"] == idempotency_key),
                None,
            )

        def create(self, db, **kwargs):
            sleep(0.01)
            job = SimpleNamespace(
                id=len(created) + 1,
                metadata_=kwargs["metadata"],
                status="running",
                error_message=None,
                created_at=now,
                started_at=now,
                finished_at=None,
            )
            created.append(job)
            return job

    service = OperationsActionService(job_repository=Repository())
    monkeypatch.setattr(operations_actions, "duckdb_write_lock", lambda *a: nullcontext())
    monkeypatch.setattr(operations_actions, "merge_job_lease_metadata", lambda db, m, **kw: m)
    monkeypatch.setattr(
        service,
        "preview",
        lambda db, **kw: OperationsActionPreview(
            action_type=kw["action_type"],
            executable=True,
            summary="ready",
            parameters=kw["parameters"],
        ),
    )
    db = SimpleNamespace(commit=lambda: None, refresh=lambda job: None)
    request = OperationsActionStartRequest(
        action_type="daily_data_sync",
        parameters={},
        idempotency_key="lost-response-key",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(service.start, db, request=request) for _ in range(2)]
        assert [future.result().action_id for future in futures] == [1, 1]
    assert len(created) == 1
    with pytest.raises(OperationsActionConflictError):
        service.start(
            db,
            request=OperationsActionStartRequest(
                action_type="alternative_data_sync",
                parameters={"sources": ["eastmoney"]},
                idempotency_key="lost-response-key",
            ),
        )


def test_price_value_preflight_rejects_history_and_same_evening(monkeypatch):
    class Repository:
        def latest_open_date(self, db, **kwargs):
            return date(2026, 11, 18)

        def completed_daily_sync(self, db, **kwargs):
            return SimpleNamespace(id=1)

    monkeypatch.setattr(operations_actions, "PriceValueScreenRepository", Repository)
    service = OperationsActionService()
    preview = service.preview(None, action_type="price_value_input_prepare", parameters={})
    assert preview.executable is True
    assert preview.parameters == {
        "price_date": "2026-11-18",
        "observation_mode": "current_research_catchup",
    }
    assert preview.checks["pit_verified"] is False
    old = service.preview(
        None, action_type="price_value_input_prepare", parameters={"price_date": "2026-11-17"}
    )
    assert old.executable is False
    with pytest.raises(ValueError, match="current_research_catchup"):
        service.preview(
            None,
            action_type="price_value_input_prepare",
            parameters={"observation_mode": "same_evening"},
        )


def test_blocked_price_value_recheck_preserves_reason_codes(monkeypatch):
    called = []
    prep = SimpleNamespace(run=lambda db, **kw: called.append(kw) or {"batch_id": 8})
    screen = SimpleNamespace(
        run=lambda db: {
            "status": "blocked",
            "global_reason_codes": ["reported_roe_missing"],
            "target_trade_date": "2026-11-18",
        }
    )
    monkeypatch.setattr(operations_actions, "PriceValueInputPrepService", lambda: prep)
    monkeypatch.setattr(operations_actions, "PriceValueScreenService", lambda: screen)
    result, status = OperationsActionService()._execute_action(
        None,
        action_id=1,
        action_type="price_value_input_prepare",
        parameters={"price_date": "2026-11-18"},
    )
    assert status == "partial" and result["problem_closed"] is False
    assert result["recheck"]["global_reason_codes"] == ["reported_roe_missing"]
    assert called == [
        {"observation_mode": "current_research_catchup", "price_date": date(2026, 11, 18)}
    ]


@pytest.mark.parametrize("phase_fails", [False, True])
def test_duplicate_background_dispatch_cannot_execute_a_terminal_action(monkeypatch, phase_fails):
    now = datetime.now(UTC)
    job = SimpleNamespace(
        id=1,
        job_name="operator_action",
        status="running",
        metadata_={"phase": "queued", "action_type": "daily_data_sync", "parameters": {}},
        error_message=None,
    )
    writes = []

    class Repository:
        def get(self, db, action_id):
            return job

        def finish(self, db, *, job_log, status, **kwargs):
            job_log.status = status
            job_log.finished_at = now

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def add(self, row):
            pass

        def commit(self):
            pass

    service = OperationsActionService(
        session_factory=Database, job_repository=Repository(), job_lock=Lock()
    )
    monkeypatch.setattr(operations_actions, "duckdb_write_lock", lambda *a: nullcontext())
    monkeypatch.setattr(
        service,
        "preview",
        lambda *a, **kw: OperationsActionPreview(
            action_type="daily_data_sync",
            executable=True,
            summary="ready",
            parameters={},
        ),
    )
    monkeypatch.setattr(
        service,
        "_execute_action",
        lambda *a, **kw: (writes.append(kw["action_id"]) or {"problem_closed": True}, "success"),
    )
    if phase_fails:
        monkeypatch.setattr(
            service,
            "_set_phase",
            lambda *a: (_ for _ in ()).throw(
                RuntimeError("phase write failed"),
            ),
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(service.execute, 1) for _ in range(2)]
        for future in futures:
            future.result()
    assert writes == ([] if phase_fails else [1])
    assert job.status == ("failed" if phase_fails else "success")


def test_profile_batch_with_one_empty_candidate_remains_partial(monkeypatch):
    result = SimpleNamespace(
        success_count=2,
        failed_count=0,
        total_profile_rows=10,
        results=[
            SimpleNamespace(factor_name="one", factor_version="v1", profile_rows=10),
            SimpleNamespace(factor_name="two", factor_version="v1", profile_rows=0),
        ],
    )
    monkeypatch.setattr(
        operations_actions,
        "FactorBatchProfilerService",
        lambda: SimpleNamespace(run=lambda *a, **kw: result),
    )
    payload, status = OperationsActionService()._execute_action(
        None,
        action_id=1,
        action_type="factor_profile_rebuild",
        parameters={"start_date": "2026-11-01", "end_date": "2026-11-02", "adjust_type": "qfq"},
    )
    assert status == "partial" and payload["problem_closed"] is False
    assert payload["recheck"]["empty_factor_examples"] == ["two@v1"]
