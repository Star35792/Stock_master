import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.routes import agent as route
from app.config.settings import settings
from app.services import agent_tools as tools_module
from app.services.agent import AgentConflictError
from app.services.agent_execution_contract import compact_preview, parameters_hash
from app.services.agent_permissions import validate_grant
from app.services.agent_state import AgentStateStore
from app.services.agent_tools import AgentTools
from app.services.operations_actions import OperationsActionService
from tests.test_agent_planning import agent, draft, step, storage as storage


class ExecutionTools:
    def __init__(self):
        self.reads, self.previews, self.starts = [], [], []
        self.preview_value = {"executable": True, "summary": "研究预检", "source_sha256": "a"}
        self.task_value = {"task_id": 71, "status": "running", "result": None}
        self.read_result = {"trade_date": "2026-11-18"}

    def read(self, name, args):
        self.reads.append((name, args))
        return deepcopy(self.read_result)

    def preview(self, name, args):
        self.previews.append((name, args))
        return deepcopy(self.preview_value)

    def start(self, *args):
        self.starts.append(args)
        return 71

    def task_for_action(self, *args):
        return deepcopy(self.task_value)


def setup(store, steps=None, *, approve=True):
    service = agent(store, draft(steps=steps or [step("sync_daily_data", "collect")]))
    tools = ExecutionTools()
    service.tools = tools
    view = service.plan("规划所给范围")
    if approve:
        p = view.plans[-1]
        view = service.decide_plan(view.session_id, p.plan_id, p.version, p.plan_hash, "approve")
    return service, tools, view


def control(service, view, operation="advance"):
    p = view.plans[-1]
    return service.executor.control(view.session_id, p.plan_id, p.version, p.plan_hash, operation)


def confirm(service, view):
    return service.decide(view.session_id, view.actions[-1].action_id, "confirm")


def test_read_dag_runs_only_inside_approval_without_model_or_write_permission(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", False)
    service, tools, view = setup(
        storage[0],
        [step(step_id="first"), step("research_overview", "second", depends_on=["first"])],
    )
    result = control(service, view)
    assert result.executions[0].status == "completed"
    assert result.executions[0].goal_verified is False
    assert [name for name, _ in tools.reads] == ["operations_overview", "research_overview"]
    control(service, view)
    assert len(tools.reads) == 2 and service.client.complete.call_count == 1
    assert not tools.starts


def test_unapproved_and_legacy_write_paths_cannot_dispatch(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(storage[0], approve=False)
    with pytest.raises(AgentConflictError):
        control(service, view)
    service.client.complete.return_value = {
        "tool_calls": [{"id": "legacy", "function": {"name": "sync_daily_data", "arguments": "{}"}}]
    }
    legacy = service.chat("同步日线")
    with pytest.raises(AgentConflictError, match="计划授权"):
        service.decide(legacy.session_id, legacy.actions[-1].action_id, "confirm")
    assert not tools.starts


def test_preflight_allowed_but_default_write_gate_stays_closed(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", False)
    service, tools, view = setup(storage[0])
    pending = control(service, view)
    assert pending.executions[0].status == "awaiting_confirmation"
    assert pending.actions[0].permission_scope == "data_maintenance"
    with pytest.raises(AgentConflictError, match="写执行仍关闭"):
        confirm(service, pending)
    assert len(tools.previews) == 1 and not tools.starts


def test_confirmation_rechecks_changed_source_and_requires_new_action(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(storage[0])
    pending = control(service, view)
    tools.preview_value["source_sha256"] = "b"
    changed, task = confirm(service, pending)
    assert task is None and not tools.starts
    assert changed.actions[0].status == "expired" and changed.actions[1].status == "pending"
    with pytest.raises(AgentConflictError):
        confirm(service, pending)
    started, task = confirm(service, changed)
    assert task == 71 and started.executions[0].status == "waiting_task"
    again, task = confirm(service, started)
    assert task is None and len(tools.starts) == 1
    assert again.actions[-1].task_id == 71


@pytest.mark.parametrize("status", ["running", "partial", "failed", "unknown"])
def test_task_waiting_and_failure_block_downstream(storage, monkeypatch, status):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(
        storage[0],
        [
            step("sync_daily_data", "collect"),
            step("operations_overview", "inspect", depends_on=["collect"]),
        ],
    )
    started, _ = confirm(service, control(service, view))
    tools.task_value = {"status": status, "result": {"run_id": 999}}
    result = control(service, started)
    assert result.executions[0].status == ("waiting_task" if status == "running" else "blocked")
    assert not tools.reads


def test_successful_exploration_keeps_qualification_false_and_uses_verified_run_id(
    storage, monkeypatch
):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    args = {
        "name": "探索",
        "factor_name": "momentum_20",
        "factor_version": "v2",
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
        "initial_cash": "100000",
        "higher_is_better": False,
    }
    text = json.dumps(args)
    steps = [
        step(
            "run_factor_backtest",
            "research",
            parameters=args,
            input_evidence={key: text for key in args if key != "name"},
        ),
        step(
            "backtest_result",
            "inspect",
            depends_on=["research"],
            bindings=[{"parameter": "run_id", "source_step": "research", "output": "run_id"}],
        ),
    ]
    service = agent(storage[0], draft(steps=steps))
    tools = ExecutionTools()
    service.tools = tools
    view = service.plan(text)
    p = view.plans[-1]
    service.decide_plan(view.session_id, p.plan_id, p.version, p.plan_hash, "approve")
    started, _ = confirm(service, control(service, view))
    tools.task_value = {
        "status": "success",
        "result": {"run_id": 42, "research_only": True, "evidence_qualified": False},
    }
    result = control(service, started)
    assert tools.reads == [("backtest_result", {"run_id": 42})]
    assert result.executions[0].steps[0].summary["evidence_qualified"] is False
    assert result.executions[0].status == "completed"
    assert result.executions[0].goal_verified is False


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"error": "failure"},
        {"status": "partial"},
        {"truncated": True},
        {"final_holdings_error": "no data"},
    ],
)
def test_incomplete_read_result_does_not_advance(storage, raw):
    service, tools, view = setup(
        storage[0],
        [step(step_id="first"), step("research_overview", "second", depends_on=["first"])],
    )
    tools.read_result = raw
    result = control(service, view)
    assert result.executions[0].status == "blocked" and len(tools.reads) == 1


def test_cancel_during_read_preserves_cancel_and_stops_remaining_dag(storage):
    service, tools, view = setup(
        storage[0],
        [step(step_id="first"), step("research_overview", "second", depends_on=["first"])],
    )
    entered, release = Event(), Event()

    def read(name, args):
        tools.reads.append((name, args))
        entered.set()
        assert release.wait(5)
        return {"trade_date": "2026-11-18"}

    tools.read = read
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(control, service, view)
        assert entered.wait(5)
        cancelled = control(service, view, "cancel")
        assert cancelled.executions[0].status == "cancelled"
        release.set()
        result = future.result(timeout=5)
    assert result.executions[0].status == "cancelled" and len(tools.reads) == 1
    assert result.executions[0].steps[1].status == "cancelled"


def test_cancel_after_start_claim_retains_late_task_without_replay(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(storage[0])
    pending = control(service, view)
    entered, release = Event(), Event()

    def start(*args):
        tools.starts.append(args)
        entered.set()
        assert release.wait(5)
        return 71

    tools.start = start
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(confirm, service, pending)
        assert entered.wait(5)
        control(service, view, "cancel")
        release.set()
        result, task_id = future.result(timeout=5)
    assert task_id == 71 and result.executions[0].status == "cancelled"
    assert result.actions[0].task_id == 71 and len(tools.starts) == 1


def test_cross_instance_duplicate_confirmation_has_one_start(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    store, factory = storage
    service, tools, view = setup(store)
    pending = control(service, view)
    second = agent(AgentStateStore(factory))
    second.tools = tools

    def run(instance):
        try:
            return confirm(instance, pending)
        except AgentConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, [service, second]))
    assert len(tools.starts) == 1


def test_unknown_start_restores_and_never_blindly_replays(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    store, factory = storage
    service, tools, view = setup(store)
    pending = control(service, view)
    tools.start = Mock(side_effect=TimeoutError("private details"))
    result, task = confirm(service, pending)
    assert task is None and result.executions[0].steps[0].status == "unknown"
    restored = agent(AgentStateStore(factory))
    restored.tools = tools
    control(restored, view)
    with pytest.raises(AgentConflictError):
        confirm(restored, pending)
    assert tools.start.call_count == 1


def test_forged_task_identity_and_missing_output_remain_unknown(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(storage[0])
    started, _ = confirm(service, control(service, view))
    tools.task_for_action = Mock(side_effect=ValueError("forged task"))
    result = control(service, started)
    assert result.executions[0].steps[0].status == "unknown"
    assert result.executions[0].steps[0].outputs == {}


def test_preview_storage_does_not_copy_holdings_and_raw_ledger(storage):
    service, tools, view = setup(storage[0])
    tools.preview_value["holdings"] = [{"symbol": "600000.SH", "quantity": 123}] * 50
    pending = control(service, view)
    assert "holdings" not in pending.actions[0].preview
    assert pending.actions[0].preview["holdings_count"] == 50
    assert len(pending.actions[0].preview["holdings_sha256"]) == 64


def test_grant_is_rechecked_against_persisted_plan_and_scope(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(storage[0])
    pending = control(service, view)
    action = pending.actions[0]
    seen = []

    def start(name, args, preview, action_id):
        validate_grant(preview["_agent_grant"], name, args, action_id, store=storage[0], now=1000)
        seen.append(1)
        return 71

    tools.start = start
    result, _ = confirm(service, pending)
    assert seen == [1]
    grant = action.preview["_agent_grant"]
    validate_grant(
        grant,
        action.tool,
        action.parameters,
        action.action_id,
        task_id=71,
        store=storage[0],
        now=1000,
    )
    control(service, result, "cancel")
    with pytest.raises(ValueError):
        validate_grant(
            grant,
            action.tool,
            action.parameters,
            action.action_id,
            task_id=71,
            store=storage[0],
            now=1000,
        )


def test_known_task_adapter_checks_exact_confirmation_tool_and_parameters(monkeypatch):
    from app.schemas.agent_recovery import AgentRecoveryRead

    args = {}
    confirmation = "confirmed-action-000000001"
    job = SimpleNamespace(
        job_name="operator_action",
        metadata_={
            "tool": "sync_daily_data",
            "confirmation_id": confirmation,
            "idempotency_key": f"agent:{confirmation}",
            "action_type": "daily_data_sync",
            "parameters": args,
            "parameters_hash": parameters_hash("sync_daily_data", args),
        },
    )

    @contextmanager
    def session():
        yield Mock()

    monkeypatch.setattr(tools_module, "SessionLocal", session)
    tools = AgentTools()
    tools.jobs = SimpleNamespace(get=lambda *_: job)
    job.metadata_["_agent_action"] = tools.action_identity(
        "sync_daily_data", {}, confirmation
    ).model_dump(mode="json")
    tools.recover_action = Mock(
        return_value=AgentRecoveryRead(
            status="verified",
            reason="matched",
            confirmation_id=confirmation,
            task_id=71,
            outputs={"task_id": 71},
        )
    )
    assert tools.task_for_action(71, confirmation, "sync_daily_data", {})["status"] == "success"
    with pytest.raises(ValueError):
        tools.task_for_action(71, "different", "sync_daily_data", {})


def test_execution_api_rejects_client_state_and_parameters(storage, monkeypatch):
    service, tools, view = setup(storage[0], [step()])
    monkeypatch.setattr(route, "service", service)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    p = view.plans[0]
    path = f"/api/v1/agent/plans/{p.plan_id}/execution"
    payload = {
        "session_id": view.session_id,
        "version": p.version,
        "plan_hash": p.plan_hash,
        "operation": "advance",
    }
    client = TestClient(app)
    assert client.post(path, json={**payload, "status": "success"}).status_code == 422
    assert client.post(path, json={**payload, "parameters": {}}).status_code == 422
    assert client.post(path, json=payload).json()["executions"][0]["status"] == "completed"


def test_actual_pydantic_adapter_result_can_complete_read_step(storage, monkeypatch):
    class Overview(BaseModel):
        trade_date: str

    @contextmanager
    def context():
        yield Mock(), Mock(), Mock()

    adapter = AgentTools()
    monkeypatch.setattr(adapter, "context", context)
    monkeypatch.setattr(
        tools_module,
        "OperationsOverviewService",
        lambda: SimpleNamespace(get_overview=lambda *a, **k: Overview(trade_date="2026-11-18")),
    )
    service, _, view = setup(storage[0], [step()])
    service.tools = adapter
    result = control(service, view)
    assert result.executions[0].status == "completed"
    assert result.executions[0].steps[0].summary["trade_date"] == "2026-11-18"


@pytest.mark.parametrize("invalidated", ["deadline", "expired", "code", "cancelled", "budget"])
def test_known_task_observation_survives_permission_expiry_without_new_dispatch(
    storage, monkeypatch, invalidated
):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(
        storage[0],
        [
            step("sync_daily_data", "collect"),
            step("operations_overview", "after", depends_on=["collect"]),
        ],
    )
    started, _ = confirm(service, control(service, view))
    if invalidated == "cancelled":
        control(service, view, "cancel")
    elif invalidated == "code":
        monkeypatch.setattr(settings, "app_code_version", "different")
    elif invalidated == "budget":
        session = service.conversation(view.session_id)
        session.executions[view.plans[0].plan_id].dispatches = 32
        session.executions[view.plans[0].plan_id].advances = 64
        service.persist(session)
    else:
        service.clock = lambda: 5000 if invalidated == "expired" else 2300
    tools.task_value = {"status": "success", "result": {"rows": 10}}
    result = control(service, started, "refresh")
    assert result.executions[0].steps[0].status == "success"
    assert len(tools.starts) == 1 and not tools.reads


def test_refresh_preserves_waiting_then_enables_continuation(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    service, tools, view = setup(
        storage[0],
        [
            step("sync_daily_data", "collect"),
            step("operations_overview", "after", depends_on=["collect"]),
        ],
    )
    started, _ = confirm(service, control(service, view))
    waiting = control(service, started, "refresh")
    assert waiting.executions[0].status == "waiting_task"
    tools.task_value = {"status": "success", "result": {}}
    ready = control(service, waiting, "refresh")
    assert ready.executions[0].status == "ready" and not tools.reads
    assert control(service, ready).executions[0].status == "completed"


def test_cost_and_evidence_diagnostics_remain_reviewable_without_holdings_copy():
    checks = [
        {
            "code": "cost_model",
            "status": "warning",
            "detail": "佣金含最低5元和2bps滑点",
            "next_action": "核对费用假设",
        },
        {
            "code": "evidence",
            "status": "warning",
            "detail": "仅探索，PIT与公司行为缺口未解除",
            "next_action": "保留资格限制",
        },
    ]
    preview = compact_preview(
        {
            "executable": True,
            "checks": {"checks": checks},
            "holdings": [{"symbol": "600000.SH"}] * 50,
        }
    )
    assert preview["checks"]["checks"] == checks and "holdings" not in preview


def test_maintenance_background_rejects_actual_job_parameter_tampering(monkeypatch):
    from app.services import agent_state

    metadata = {
        "action_type": "daily_data_sync",
        "parameters": {"unexpected": True},
        "idempotency_key": "agent:confirmation",
        "parameters_hash": parameters_hash("sync_daily_data", {}),
        "agent_authorization": {"session_id": "session"},
    }
    job = SimpleNamespace(id=71, job_name="operator_action", status="running", metadata_=metadata)
    monkeypatch.setattr(
        agent_state,
        "AgentStateStore",
        lambda: SimpleNamespace(
            load=lambda _: {
                "actions": {
                    "confirmation": {
                        "view": {"task_id": 71, "action_id": "confirmation", "parameters": {}}
                    }
                }
            }
        ),
    )

    @contextmanager
    def session():
        yield Mock()

    monkeypatch.setattr(tools_module, "SessionLocal", session)
    adapter = AgentTools()
    adapter.jobs = SimpleNamespace(get=lambda *_: job, finish=Mock())
    adapter.operations.execute = Mock()
    adapter.execute(71)
    adapter.operations.execute.assert_not_called()
    assert adapter.jobs.finish.call_args.kwargs["status"] == "failed"


def test_shared_operations_executor_cannot_bypass_agent_grant(monkeypatch):
    job = SimpleNamespace(
        job_name="operator_action",
        status="running",
        metadata_={"idempotency_key": "agent:confirmation"},
    )

    @contextmanager
    def session():
        yield Mock()

    service = OperationsActionService(session_factory=session)
    service.job_repository = SimpleNamespace(get=lambda *_: job)
    service.preview = Mock()
    monkeypatch.setattr(
        AgentTools, "_validate_execution_grant", Mock(side_effect=ValueError("deny"))
    )
    with pytest.raises(ValueError):
        service._execute_locked(71)
    service.preview.assert_not_called()
