import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent as route
from app.config.settings import Settings
from app.services.agent import AgentService
from app.services.agent_stream import AgentStream, AgentStreamStopped
from app.services.deepseek import AgentUnavailableError, DeepSeekClient
from tests.test_agent import FakeTools, MemoryStore, call, service
from tests.test_agent_planning import draft, response, step


def chunks(*deltas, finish="stop", done=True):
    data = b"".join(
        (
            "data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "delta": delta,
                            "finish_reason": None,
                        }
                    ]
                }
            )
            + "\n\n"
        ).encode()
        for delta in deltas
    )
    data += (
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}) + "\n\n"
    ).encode()
    return data + (b"data: [DONE]\n\n" if done else b"")


def provider(monkeypatch, raw):
    class Upstream(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    opened = Mock(return_value=Upstream(raw))
    monkeypatch.setattr(
        "app.services.deepseek.build_opener", lambda *_: SimpleNamespace(open=opened)
    )
    return DeepSeekClient(Settings(_env_file=None, deepseek_api_key="test-key")), opened


def test_real_transport_forwards_deltas_and_ignores_reasoning(monkeypatch):
    client, opened = provider(
        monkeypatch,
        chunks(
            {"reasoning_content": "private thought"},
            {"content": "你"},
            {"content": "好"},
        ),
    )
    output = []
    result = client.stream_complete([], [], on_text=output.append, check=lambda: None)
    assert output == ["你", "好"] and result["content"] == "你好"
    assert "private thought" not in json.dumps(result)
    assert json.loads(opened.call_args.args[0].data)["stream"] is True


def test_tool_fragments_are_assembled_before_dispatch(monkeypatch):
    client, _ = provider(
        monkeypatch,
        chunks(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {
                            "name": "market_summary",
                            "arguments": '{"sym',
                        },
                    }
                ]
            },
            {"tool_calls": [{"index": 0, "function": {"arguments": 'bol":"600000.SH"}'}}]},
            finish="tool_calls",
        ),
    )
    result = client.stream_complete([], [], on_text=lambda _: None, check=lambda: None)
    assert result["tool_calls"][0]["function"]["arguments"] == '{"symbol":"600000.SH"}'


@pytest.mark.parametrize("finish,done", [("stop", False), ("length", True), (None, True)])
def test_truncated_upstream_is_never_success(monkeypatch, finish, done):
    client, _ = provider(monkeypatch, chunks({"content": "半句"}, finish=finish, done=done))
    with pytest.raises(AgentUnavailableError):
        client.stream_complete([], [], on_text=lambda _: None, check=lambda: None)


def test_cancelled_provider_does_not_open_network(monkeypatch):
    client, opened = provider(monkeypatch, chunks())

    def stopped():
        raise AgentStreamStopped()

    with pytest.raises(AgentStreamStopped):
        client.stream_complete([], [], on_text=lambda _: None, check=stopped)
    assert not opened.called


@contextmanager
def api(monkeypatch, agent):
    monkeypatch.setattr(route, "service", agent)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client


def events(response):
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def test_stream_sends_saved_identity_tools_and_authoritative_done(monkeypatch):
    agent, tools, _ = service(call("operations_overview"), {"content": "具体的查询结果"})
    with api(monkeypatch, agent) as client:
        result = client.post("/api/v1/agent/chat/stream", json={"message": "检查质量"})
    data = events(result)
    assert data[0]["type"] == "session"
    assert any(item["type"] == "tool_start" for item in data)
    assert any(item["type"] == "tool_end" for item in data)
    assert data[-1]["type"] == "done"
    saved = agent.store.load(data[0]["session_id"])
    assert saved["messages"] == data[-1]["session"]["messages"]
    assert len(tools.reads) == 1 and not tools.starts


def test_stream_resets_tool_preface_and_text_is_delivered_before_provider_finishes(monkeypatch):
    seen = []
    stream = AgentStream()
    rounds = [0]

    def complete(messages, tools, *, on_text, check):
        rounds[0] += 1
        on_text("先查数据" if rounds[0] == 1 else "实际答复")
        assert any("event: text" in frame for frame in list(stream.queue.queue))
        return call("operations_overview") if rounds[0] == 1 else {"content": "实际答复"}

    agent = AgentService(
        client=SimpleNamespace(stream_complete=complete), tools=FakeTools(), store=MemoryStore()
    )
    view = agent.chat("检查", events=stream)
    while not stream.queue.empty():
        seen.append(json.loads(stream.queue.get().split("data: ", 1)[1]))
    assert sum(item["type"] == "round" for item in seen) == 2
    assert any(item["type"] == "draft_reset" for item in seen)
    assert view.messages[-1]["content"] == "实际答复"


def test_plan_chat_bubble_contains_actual_missing_question(monkeypatch):
    agent, tools, _ = service(
        response(
            draft(
                intent="position_analysis",
                clarification_questions=["请提供要分析的账本ID。"],
                steps=[step("position_holdings")],
            )
        )
    )
    with api(monkeypatch, agent) as client:
        data = events(client.post("/api/v1/agent/plans/stream", json={"message": "分析持仓"}))
    assert "请提供要分析的账本ID" in data[-1]["session"]["messages"][-1]["content"]
    assert not tools.reads and not tools.starts


def test_save_failure_cannot_send_done(monkeypatch):
    agent, _, _ = service({"content": "回答"})
    save = agent.store.save
    calls = [0]

    def failing_save(state):
        calls[0] += 1
        if calls[0] > 1:
            raise RuntimeError("private storage details")
        return save(state)

    agent.store.save = failing_save
    with api(monkeypatch, agent) as client:
        data = events(client.post("/api/v1/agent/chat/stream", json={"message": "问答"}))
    assert data[-1]["type"] == "error"
    assert not any(item["type"] == "done" for item in data)
    assert "private storage details" not in json.dumps(data)


def test_disconnect_blocks_next_tool_and_preserves_completed_observation():
    agent, tools, _ = service(call("operations_overview"), call("quality_issues"))
    stream = AgentStream()
    read = tools.read

    def stop_after_read(name, args):
        result = read(name, args)
        stream.stopped.set()
        return result

    tools.read = stop_after_read
    with pytest.raises(AgentStreamStopped):
        agent.chat("检查", events=stream)
    saved = next(iter(agent.store.rows.values()))
    assert len(tools.reads) == 1
    assert saved["steps"][0]["tool"] == "operations_overview"
    assert "未完成" in saved["messages"][-1]["content"]


@pytest.mark.parametrize("stop_before_first", [True, False])
def test_interrupted_batch_pairs_every_call_and_restores_without_replay(stop_before_first):
    batch = call("operations_overview")
    second = call("quality_issues")["tool_calls"][0]
    second["id"] = "call-2"
    batch["tool_calls"].append(second)
    agent, tools, _ = service(batch)
    stream = AgentStream()
    emit, read = stream.emit, tools.read

    def stopping_emit(kind, **data):
        if stop_before_first and kind == "tool_start":
            stream.stopped.set()
        emit(kind, **data)

    def stopping_read(name, args):
        result = read(name, args)
        stream.stopped.set()
        return result

    stream.emit = stopping_emit
    tools.read = stopping_read
    with pytest.raises(AgentStreamStopped):
        agent.chat("检查", events=stream)
    saved = next(iter(agent.store.rows.values()))
    results = [item for item in saved["history"] if item["role"] == "tool"]
    assert [item["tool_call_id"] for item in results] == ["call-1", "call-2"]
    assert json.loads(results[-1]["content"])["execution_status"] == "unverified"
    expected_reads = 0 if stop_before_first else 1
    assert len(tools.reads) == expected_reads

    def resumed_response(messages, definitions):
        pending = set()
        for item in messages:
            if item["role"] == "tool":
                pending.remove(item["tool_call_id"])
            else:
                assert not pending
                pending.update(call["id"] for call in item.get("tool_calls", []))
        assert not pending
        return {"content": "此前中断的调用未核验。"}

    restored = AgentService(
        client=SimpleNamespace(complete=resumed_response),
        tools=tools,
        store=agent.store,
        clock=agent.clock,
    )
    view = restored.chat("解释已保存结果", saved["session_id"])
    assert view.messages[-1]["content"] == "此前中断的调用未核验。"
    assert len(tools.reads) == expected_reads


def test_plan_stopped_during_provider_wait_does_not_save_new_draft():
    agent, tools, _ = service()
    stream = AgentStream()

    def complete(*args, **kwargs):
        stream.stopped.set()
        return response(draft())

    agent.client.complete = complete
    with pytest.raises(AgentStreamStopped):
        agent.plan("检查质量", events=stream)
    saved = next(iter(agent.store.rows.values()))
    assert saved["plans"] == []
    assert not tools.reads and not tools.starts
