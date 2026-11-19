from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.api.routes import agent as route
from app.config.settings import settings
from app.database.agent_session import create_agent_engine, initialize_agent_database
from app.repositories.agent import AgentStateConflict
from app.schemas.agent import AgentMemoryWrite
from app.services.agent import AgentConflictError, AgentService
from app.services.agent_state import AgentStateStore, AgentStorageUnavailableError
from tests.test_agent import prepare_sync


@pytest.fixture
def storage(tmp_path):
    engine = create_agent_engine(str(tmp_path / "agent.db"))
    initialize_agent_database(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield AgentStateStore(factory), factory
    engine.dispose()


def make_agent(store, *, clock=lambda: 1000, response=None, tools=None):
    return AgentService(
        store=store,
        tools=tools or Mock(),
        clock=clock,
        client=SimpleNamespace(complete=Mock(return_value=response or {"content": "研究结果"})),
    )


def proposal_response():
    return {
        "tool_calls": [
            {
                "id": "call-1",
                "function": {
                    "name": "sync_daily_data",
                    "arguments": "{}",
                },
            }
        ]
    }


def test_sqlite_is_separate_and_durable(storage):
    store, factory = storage
    assert set(inspect(factory.kw["bind"]).get_table_names()) == {"agent_session", "agent_memory"}
    with factory() as db:
        assert db.scalar(text("PRAGMA journal_mode")) == "wal"
        assert db.scalar(text("PRAGMA synchronous")) == 2
    agent = make_agent(store)
    result = agent.chat("请查询研究结果")
    restored = make_agent(AgentStateStore(factory)).get(result.session_id)
    assert restored.messages == result.messages
    assert restored.steps == result.steps
    assert store.list_sessions()[0]["title"] == "请查询研究结果"


def test_revision_compare_and_swap_rejects_stale_snapshot(storage):
    store, _ = storage
    state = {"session_id": "s" * 32, "revision": 0, "messages": []}
    state["revision"] = store.save(state)
    stale = dict(state)
    state["revision"] = store.save(state)
    with pytest.raises(AgentStateConflict):
        store.save(stale)
    assert store.load(state["session_id"])["revision"] == 2


def test_pending_actions_restore_but_expired_or_changed_code_cannot_execute(storage, monkeypatch):
    store, factory = storage
    tools = Mock()
    tools.preview.return_value = {"executable": True, "summary": "待确认"}
    agent = make_agent(store, response=proposal_response(), tools=tools)
    result = agent.chat("同步日线")
    restored = make_agent(AgentStateStore(factory), clock=lambda: 1601, tools=tools)
    assert restored.get(result.session_id).actions[0].status == "expired"
    with pytest.raises(AgentConflictError):
        restored.decide(result.session_id, result.actions[0].action_id, "confirm")
    tools.start.assert_not_called()
    monkeypatch.setattr(settings, "app_code_version", "changed-code")
    assert (
        make_agent(AgentStateStore(factory)).get(result.session_id).actions[0].status == "expired"
    )


def test_confirmation_intent_survives_failure_and_is_never_blindly_replayed(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    store, factory = storage
    tools = Mock()
    tools.preview.return_value = {"executable": True}
    tools.start.side_effect = RuntimeError("simulated connection loss after task creation")
    agent = make_agent(store, response=proposal_response(), tools=tools)
    result = prepare_sync(agent)
    unknown, task_id = agent.decide(result.session_id, result.actions[0].action_id, "confirm")
    assert task_id is None and unknown.executions[0].steps[0].status == "unknown"
    restored = make_agent(AgentStateStore(factory), tools=tools)
    assert restored.get(result.session_id).actions[0].status == "starting"
    with pytest.raises(AgentConflictError, match="不能重复执行"):
        restored.decide(result.session_id, result.actions[0].action_id, "confirm")
    assert tools.start.call_count == 1


def test_phase_one_write_gate_blocks_confirm_even_with_valid_action(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", False)
    store, _ = storage
    tools = Mock()
    tools.preview.return_value = {"executable": True}
    agent = make_agent(store, response=proposal_response(), tools=tools)
    result = agent.chat("同步")
    with pytest.raises(AgentConflictError, match="写执行仍关闭"):
        agent.decide(result.session_id, result.actions[0].action_id, "confirm")
    tools.start.assert_not_called()


def test_memory_is_explicit_versioned_editable_and_deletable(storage):
    store, factory = storage
    result = make_agent(store).chat("我偏好低波动")
    assert store.memories() == []
    memory = store.save_memory(
        AgentMemoryWrite(
            kind="preference", content="优先解释数据日期", source_session_id=result.session_id
        )
    )[0]
    assert AgentStateStore(factory).memories() == [memory]
    updated = store.save_memory(
        AgentMemoryWrite(
            id=memory["id"], revision=1, kind="preference", content="优先解释数据日期与证据缺口"
        )
    )[0]
    assert updated["revision"] == 2
    with pytest.raises(AgentStateConflict):
        store.delete_memory(memory["id"], 1)
    assert store.delete_memory(memory["id"], 2) == []


def test_removed_memory_is_not_injected_into_new_model_requests(storage):
    store, _ = storage
    memory = store.save_memory(AgentMemoryWrite(kind="constraint", content="优先报告缺失证据"))[0]
    agent = make_agent(store)
    result = agent.chat("分析")
    assert "优先报告缺失证据" in agent.client.complete.call_args.args[0][0]["content"]
    store.delete_memory(memory["id"], memory["revision"])
    agent.chat("继续", result.session_id)
    assert "优先报告缺失证据" not in agent.client.complete.call_args.args[0][0]["content"]


def test_memories_reject_credentials_and_stay_bounded(storage):
    store, _ = storage
    with pytest.raises(ValidationError):
        AgentMemoryWrite(kind="preference", content="api_key=do-not-store")
    for index in range(32):
        store.save_memory(AgentMemoryWrite(kind="lesson", content=f"经验{index}"))
    with pytest.raises(ValueError, match="32"):
        store.save_memory(AgentMemoryWrite(kind="lesson", content="第33条"))
    with pytest.raises(ValueError, match="256KiB"):
        store.save({"session_id": "s" * 32, "revision": 0, "messages": ["x" * 270000]})


def test_memory_api_and_history_require_no_model_call(storage, monkeypatch):
    store, _ = storage
    agent = make_agent(store)
    monkeypatch.setattr(route, "service", agent)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    client = TestClient(app)
    assert client.get("/api/v1/agent/sessions").json() == []
    result = client.post(
        "/api/v1/agent/memories", json={"kind": "preference", "content": "先说数据日期"}
    )
    assert result.status_code == 200
    memory = result.json()[0]
    assert client.delete(f"/api/v1/agent/memories/{memory['id']}?revision=1").status_code == 200
    agent.client.complete.assert_not_called()


def test_storage_failure_is_safe_and_never_calls_model(storage, monkeypatch):
    store, _ = storage
    agent = make_agent(store)
    failure = OperationalError("private-sql", {"secret": "do-not-echo"}, Exception("private-path"))
    monkeypatch.setattr(store.repository, "save", Mock(side_effect=failure))
    monkeypatch.setattr(route, "service", agent)
    app = FastAPI(debug=True)
    app.include_router(route.router, prefix="/api/v1")
    response = TestClient(app).post("/api/v1/agent/chat", json={"message": "研究"})
    assert response.status_code == 503
    assert "private" not in response.text and "do-not-echo" not in response.text
    assert not agent.sessions
    agent.client.complete.assert_not_called()
    assert store.repository.save.call_count == 1


def test_failed_final_save_invalidates_cache_and_recovers_durable_snapshot(storage, monkeypatch):
    store, _ = storage
    agent = make_agent(store)
    save = store.repository.save
    calls = []

    def fail_second(db, state):
        calls.append(1)
        if len(calls) == 2:
            raise OperationalError("private", {}, Exception("locked"))
        return save(db, state)

    monkeypatch.setattr(store.repository, "save", fail_second)
    with pytest.raises(AgentStorageUnavailableError):
        agent.chat("查询")
    assert not agent.sessions
    saved = store.list_sessions()[0]
    recovered = agent.get(saved["session_id"])
    assert recovered.messages == [{"role": "user", "content": "查询"}]
    assert len(calls) == 2


def test_failed_confirmation_intent_is_not_executed_or_left_in_cache(storage, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    store, _ = storage
    tools = Mock()
    tools.preview.return_value = {"executable": True}
    agent = make_agent(store, response=proposal_response(), tools=tools)
    proposal = prepare_sync(agent)
    with monkeypatch.context() as patcher:
        patcher.setattr(
            store.repository,
            "save",
            Mock(side_effect=OperationalError("private", {}, Exception("locked"))),
        )
        with pytest.raises(AgentStorageUnavailableError):
            agent.decide(proposal.session_id, proposal.actions[0].action_id, "confirm")
    tools.start.assert_not_called()
    assert agent.get(proposal.session_id).actions[0].status == "pending"
