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


def test_fork_route_accepts_only_saved_user_messages_and_restores_empty_branch(
    storage, monkeypatch
):
    store, _ = storage
    agent = make_agent(store)
    source = agent.chat("分析第一套假设")
    monkeypatch.setattr(route, "service", agent)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    client = TestClient(app)
    path = f"/api/v1/agent/sessions/{source.session_id}/fork"

    for index in (1, 2, -1, "0", True):
        assert client.post(path, json={"message_index": index}).status_code == 422
    assert (
        client.post(
            path.replace(source.session_id, "missing"), json={"message_index": 0}
        ).status_code
        == 404
    )
    assert len(store.list_sessions()) == 1

    response = client.post(path, json={"message_index": 0})
    assert response.status_code == 201
    branch = response.json()
    assert branch["session_id"] != source.session_id
    assert branch["messages"] == []
    assert branch["forked_from"] == {"session_id": source.session_id, "message_index": 0}
    assert client.get(f"/api/v1/agent/sessions/{branch['session_id']}").json() == branch
    assert any(item["title"].startswith("分支") for item in store.list_sessions())
    assert agent.client.complete.call_count == 1


def test_fork_prunes_model_context_to_fit_an_existing_long_transcript(storage):
    store, factory = storage
    prefix = [
        message
        for index in range(11)
        for message in (
            {"role": "user", "content": f"第{index}组研究"},
            {"role": "assistant", "content": "结果" * 3400},
        )
    ]
    question = {"role": "user", "content": "换一组假设"}
    store.save({"session_id": "source", "messages": [*prefix, question], "history": [question]})

    branch = make_agent(store).fork("source", len(prefix))
    assert branch.messages == prefix
    saved = store.load(branch.session_id)
    assert saved["history"] == prefix[-2:]

    restored = make_agent(AgentStateStore(factory)).chat("继续", branch.session_id)
    assert restored.messages[:-2] == prefix
    assert store.load("source")["messages"] == [*prefix, question]


def test_long_conversation_windows_model_requests_and_preserves_saved_transcript(storage):
    from app.services.agent_context import HISTORY_BYTES, context_size
    from app.services.agent_stream import AgentStream

    store, factory = storage
    reply = {"content": "研究说明" * 750}
    agent = make_agent(store, response=reply)
    first = "第一组研究" + "甲" * 2500
    view = agent.chat(first)
    for index in range(1, 8):
        view = agent.chat(f"补充第{index}组" + "乙" * 2500, view.session_id)
    events = AgentStream()
    events.emit = Mock()
    view = agent.chat("按最近条件继续", view.session_id, events=events)
    model_messages = agent.client.complete.call_args.args[0]
    assert context_size(model_messages[1:]) <= HISTORY_BYTES
    assert model_messages[1]["role"] == "user"
    assert model_messages[-1]["content"] == "按最近条件继续"
    assert first not in [item["content"] for item in model_messages]
    events.emit.assert_any_call("phase", context_windowed=True)
    restored = make_agent(AgentStateStore(factory)).get(view.session_id)
    assert restored.messages == view.messages
    assert restored.messages[0]["content"] == first
    assert len(restored.messages) == 18


def test_context_window_keeps_tool_calls_and_results_in_the_same_turn():
    from app.services.agent_context import context_size, recent_turns

    old = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧答复"}]
    current = [
        {"role": "user", "content": "核查数据"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "quality-check"}]},
        {"role": "tool", "tool_call_id": "quality-check", "content": "{}"},
        {"role": "assistant", "content": "当前结果"},
    ]
    assert recent_turns(old + current, context_size(current)) == current
    assert recent_turns(old + current, context_size(current) - 1) == []


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
    result = prepare_sync(agent)
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
    monkeypatch.setattr(settings, "agent_data_maintenance_enabled", False)
    store, _ = storage
    tools = Mock()
    tools.preview.return_value = {"executable": True}
    agent = make_agent(store, response=proposal_response(), tools=tools)
    result = prepare_sync(agent)
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
