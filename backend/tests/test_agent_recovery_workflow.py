from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent as route
from app.config.settings import settings
from app.schemas.agent_recovery import AgentRecoveryRead
from app.services import agent_read_recovery
from app.services.agent import AgentService
from app.services.agent_plan_recovery import AgentPlanRecoveryService
from app.services.agent_recovery import ReadRecoveryPolicy
from app.services.agent_state import AgentStateStore
from tests.test_agent_execution import confirm, control, setup
from tests.test_agent_planning import step, storage as storage
from tests.test_agent_recovery import Clock


def test_plan_reads_retry_transient_errors_and_persist_attempts(storage, monkeypatch):
    service, tools, view = setup(storage[0], [step()])
    clock = Clock()
    service.clock = clock
    monkeypatch.setattr(
        agent_read_recovery,
        "ReadRecoveryPolicy",
        lambda **kw: ReadRecoveryPolicy(clock=clock, wait=clock.wait),
    )
    tools.read = Mock(
        side_effect=[TimeoutError(), ConnectionResetError(), {"trade_date": "2026-11-18"}]
    )
    result = control(service, view)
    assert tools.read.call_count == 3
    state = result.executions[0].steps[0]
    assert result.executions[0].status == "completed"
    assert state.attempts == 3 and state.read_checkpoint["status"] == "success"
    restored = AgentService(store=AgentStateStore(storage[1])).get(view.session_id)
    assert restored.executions[0].steps[0].read_checkpoint == state.read_checkpoint


def test_permanent_read_error_stops_plan_and_independent_pending_steps(storage):
    service, tools, view = setup(storage[0], [step(), step("research_overview", "second")])
    tools.read = Mock(side_effect=ValueError("financial gate failed"))
    result = control(service, view)
    assert tools.read.call_count == 1
    assert result.executions[0].status == "blocked"
    assert result.executions[0].steps[0].read_checkpoint["status"] == "failed"
    assert result.executions[0].steps[1].status == "pending"


def test_cancellation_between_read_check_and_reservation_does_not_call(storage, monkeypatch):
    service, tools, view = setup(storage[0], [step()])
    original_reload = service.reload
    reading_loads = 0

    def reload(session):
        nonlocal reading_loads
        original_reload(session)
        for execution in session.executions.values():
            row = execution.steps[0]
            if row.status == "reading" and (row.read_checkpoint or {}).get("status") == "ready":
                reading_loads += 1
                if reading_loads == 2:
                    execution.status = "cancelled"
                    service.persist(session)

    monkeypatch.setattr(service, "reload", reload)
    tools.read = Mock(return_value={})
    result = control(service, view)
    tools.read.assert_not_called()
    assert result.executions[0].status == "cancelled"
    assert result.executions[0].steps[0].read_checkpoint["status"] == "cancelled"


def unknown_write(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(storage[0])
    pending = control(service, view)
    tools.start = Mock(side_effect=RuntimeError("response lost"))
    unknown, task_id = confirm(service, pending)
    assert task_id is None and unknown.executions[0].steps[0].status == "unknown"
    return service, tools, unknown


def recover(service, view):
    p = view.plans[-1]
    return AgentPlanRecoveryService(service).recover(
        view.session_id, p.plan_id, p.version, p.plan_hash, p.steps[0].step_id
    )


def evidence(view, status="verified"):
    return AgentRecoveryRead(
        status=status,
        reason="原任务核验",
        confirmation_id=view.actions[0].action_id,
        task_id=71,
        outputs={"task_id": 71} if status == "verified" else {},
        artifact_type="backtest",
        artifact_id=42,
        artifact_status="success",
        artifact_verified=True,
    )


@pytest.mark.parametrize(
    "status", ["verified", "running", "partial", "failed", "unknown", "conflict"]
)
def test_explicit_recovery_only_reconciles_original_task_and_blocks_uncertain(
    storage, monkeypatch, status
):
    service, tools, view = unknown_write(storage, monkeypatch)
    tools.recover_action = Mock(return_value=evidence(view, status))
    result = recover(service, view)
    assert tools.start.call_count == 1
    state = result.executions[0].steps[0]
    assert state.summary["recovery"]["artifact_id"] == 42
    assert result.executions[0].status == (
        "completed"
        if status == "verified"
        else "waiting_task"
        if status == "running"
        else "blocked"
    )
    assert not result.executions[0].goal_verified


def test_expired_plan_still_inspects_original_evidence_without_renewing_permission(
    storage, monkeypatch
):
    service, tools, view = unknown_write(storage, monkeypatch)
    service.clock = lambda: 100000
    tools.recover_action = Mock(return_value=evidence(view))
    result = recover(service, view)
    assert result.executions[0].status == "blocked"
    assert result.executions[0].steps[0].summary["recovery"]["status"] == "verified"
    assert tools.start.call_count == 1


def test_cancel_during_evidence_query_preserves_late_artifact_without_resuming(
    storage, monkeypatch
):
    service, tools, view = unknown_write(storage, monkeypatch)
    entered, release = Event(), Event()

    def inspect(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return evidence(view)

    tools.recover_action = inspect
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(recover, service, view)
        assert entered.wait(5)
        control(service, view, "cancel")
        release.set()
        result = future.result(timeout=5)
    assert result.executions[0].status == "cancelled"
    assert result.executions[0].steps[0].summary["recovery"]["artifact_id"] == 42
    assert tools.start.call_count == 1


@pytest.mark.parametrize("status", ["verified", "running"])
def test_recovery_does_not_clear_another_step_failure(storage, monkeypatch, status):
    service, tools, view = unknown_write(storage, monkeypatch)
    session = service.conversation(view.session_id)
    from app.schemas.agent_execution import AgentStepExecution

    with session.lock:
        session.executions[view.plans[0].plan_id].steps.extend(
            [
                AgentStepExecution(step_id="failed", status="failed", reason="原数据资格未通过"),
                AgentStepExecution(step_id="pending"),
            ]
        )
        service.persist(session)
    tools.recover_action = Mock(return_value=evidence(view, status))
    result = recover(service, view)
    assert result.executions[0].steps[0].status == (
        "success" if status == "verified" else "waiting_task"
    )
    assert result.executions[0].status == "blocked"
    assert result.executions[0].steps[2].status == "pending"
    if status == "running":
        tools.task_value = {"status": "success", "result": {}, "task_id": 71}
        refreshed = control(service, view, "refresh")
        assert refreshed.executions[0].status == "blocked"
        assert not tools.reads


def test_restored_starting_intent_can_be_inspected_without_replaying(storage, monkeypatch):
    service, tools, view = unknown_write(storage, monkeypatch)
    session = service.conversation(view.session_id)
    with session.lock:
        session.executions[view.plans[0].plan_id].steps[0].status = "starting"
        service.persist(session)
    restored = AgentService(store=AgentStateStore(storage[1]), tools=tools, clock=lambda: 1000)
    tools.recover_action = Mock(return_value=evidence(view))
    result = recover(restored, view)
    assert result.executions[0].status == "completed" and tools.start.call_count == 1


def test_recovery_endpoint_rejects_forged_state_and_returns_server_checkpoint(storage, monkeypatch):
    service, tools, view = unknown_write(storage, monkeypatch)
    tools.recover_action = Mock(return_value=evidence(view, "failed"))
    monkeypatch.setattr(route, "service", service)
    app = FastAPI()
    app.include_router(route.router)
    p = view.plans[0]
    body = {
        "session_id": view.session_id,
        "version": p.version,
        "plan_hash": p.plan_hash,
        "step_id": p.steps[0].step_id,
    }
    with TestClient(app) as client:
        assert (
            client.post(
                f"/agent/plans/{p.plan_id}/recovery", json={**body, "status": "success"}
            ).status_code
            == 422
        )
        response = client.post(f"/agent/plans/{p.plan_id}/recovery", json=body)
    assert response.status_code == 200
    assert response.json()["executions"][0]["status"] == "blocked"
    assert tools.start.call_count == 1


@pytest.mark.parametrize("saved_status", ["calling", "retryable"])
def test_new_process_converges_lost_read_call_without_replaying_unknown(storage, saved_status):
    service, tools, view = setup(storage[0], [step()])
    done = control(service, view)
    session = service.conversation(view.session_id)
    with session.lock:
        execution = session.executions[view.plans[0].plan_id]
        row = execution.steps[0]
        row.status, execution.status = "reading", "running"
        row.read_checkpoint.update(status=saved_status, attempts=1, next_retry_at=1000)
        service.persist(session)
    new_tools = Mock()
    new_tools.read.return_value = {"trade_date": "2026-11-18"}
    restored = AgentService(store=AgentStateStore(storage[1]), tools=new_tools, clock=lambda: 1000)
    snapshot = restored.get(done.session_id)
    state = snapshot.executions[0].steps[0]
    new_tools.read.assert_not_called()
    if saved_status == "calling":
        assert state.status == "unknown" and state.read_checkpoint["status"] == "unknown"
        assert control(restored, snapshot).executions[0].status == "blocked"
        new_tools.read.assert_not_called()
    else:
        assert state.status == "waiting_read" and state.read_checkpoint["attempts"] == 1
        result = control(restored, snapshot)
        assert result.executions[0].status == "completed"
        assert result.executions[0].steps[0].read_checkpoint["attempts"] == 2
        assert new_tools.read.call_count == 1
