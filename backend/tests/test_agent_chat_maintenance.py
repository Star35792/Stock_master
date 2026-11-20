import json

import pytest

from app.config.settings import settings
from app.services.agent import AgentConflictError
from app.services.agent_write_policy import write_enabled
from tests.test_agent import call, service
from tests.test_agent_execution import ExecutionTools, control
from tests.test_agent_planning import draft, step


def test_chat_sync_request_creates_reviewable_plan_without_business_io():
    payload = draft(
        goal="同步行情和因子",
        steps=[step("sync_data_and_factors", "sync")],
    )
    agent, tools, _ = service(call("submit_research_plan", payload))
    view = agent.chat("帮我同步一下数据和因子")
    assert view.plans[-1].steps[0].tool == "sync_data_and_factors"
    assert view.plans[-1].status == "draft"
    assert not view.actions and not tools.starts and not tools.previews and not tools.reads
    definitions = agent.client.complete.call_args.args[1]
    names = {item["function"]["name"] for item in definitions}
    assert "submit_research_plan" in names and "sync_daily_data" not in names


def test_chat_clarification_revises_original_plan_and_keeps_explicit_input_evidence():
    first = draft(steps=[step("repair_daily_gaps", "repair")])
    text = "600000.SH，2026-11-01 至 2026-11-02"
    parameters = {"symbols": ["600000.SH"], "start_date": "2026-11-01", "end_date": "2026-11-02"}
    second = draft(
        steps=[
            step(
                "repair_daily_gaps",
                "repair",
                parameters=parameters,
                input_evidence={key: text for key in parameters},
            )
        ]
    )
    agent, tools, _ = service(
        call("submit_research_plan", first), call("submit_research_plan", second)
    )
    missing = agent.chat("帮我回填日线缺口")
    assert missing.plans[-1].status == "needs_input"
    ready = agent.chat(text, missing.session_id)
    assert [p.status for p in ready.plans] == ["superseded", "draft"]
    assert ready.plans[-1].steps[0].parameters == parameters
    prompt = agent.client.complete.call_args.args[0][0]["content"]
    assert "待承接计划" in prompt and "repair_daily_gaps" in prompt
    assert not tools.starts and not tools.previews


def test_optional_profile_categories_are_not_missing_inputs():
    text = "重建画像，2026-11-01 至 2026-11-02"
    args = {"start_date": "2026-11-01", "end_date": "2026-11-02"}
    payload = draft(
        steps=[
            step(
                "rebuild_factor_profiles",
                parameters=args,
                input_evidence={key: text for key in args},
            )
        ]
    )
    agent, _, _ = service(call("submit_research_plan", payload))
    plan = agent.chat(text).plans[-1]
    assert plan.status == "draft" and not plan.missing_inputs
    assert plan.steps[0].parameters["categories"] is None


def test_maintenance_permission_does_not_enable_research_or_skip_confirmation(monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", False)
    monkeypatch.setattr(settings, "agent_data_maintenance_enabled", True)
    agent, _, _ = service(
        call(
            "submit_research_plan",
            draft(
                steps=[step("sync_data_and_factors", "sync")],
            ),
        )
    )
    tools = ExecutionTools()
    agent.tools = tools
    view = agent.chat("同步数据和因子")
    plan = view.plans[-1]
    approved = agent.decide_plan(
        view.session_id, plan.plan_id, plan.version, plan.plan_hash, "approve"
    )
    assert not tools.starts and not tools.previews
    pending = control(agent, approved)
    assert len(tools.previews) == 1 and not tools.starts
    agent.decide(pending.session_id, pending.actions[-1].action_id, "confirm")
    assert len(tools.starts) == 1
    assert write_enabled("sync_data_and_factors")
    assert not write_enabled("run_factor_backtest")
    assert not write_enabled("create_portfolio_from_backtest")
    assert not write_enabled("execute_sql")
    status = agent.status()
    assert status["write_enabled"] is False and status["data_maintenance_enabled"] is True
    assert next(t for t in status["tools"] if t["name"] == "sync_daily_data")["write_enabled"]


def test_chat_cannot_approve_plan_or_confirm_using_free_text(monkeypatch):
    monkeypatch.setattr(settings, "agent_max_steps", 1)
    agent, tools, _ = service(
        call("submit_research_plan", draft(steps=[step("sync_daily_data")])),
        call("sync_daily_data"),
    )
    view = agent.chat("同步日线")
    view = agent.chat("确认执行", view.session_id)
    assert view.plans[-1].status == "draft" and not view.actions
    assert not tools.starts and not tools.previews
    with pytest.raises(AgentConflictError):
        control(agent, view)


def test_invalid_plan_does_not_replace_an_approved_plan(monkeypatch):
    monkeypatch.setattr(settings, "agent_max_steps", 1)
    payload = draft(steps=[step("sync_daily_data")])
    agent, _, _ = service(
        call("submit_research_plan", payload),
        call("submit_research_plan", {**payload, "steps": [step("shell")]}),
    )
    view = agent.chat("同步日线")
    plan = view.plans[-1]
    agent.decide_plan(view.session_id, plan.plan_id, plan.version, plan.plan_hash, "approve")
    rejected = agent.chat("重新安排", view.session_id)
    assert len(rejected.plans) == 1 and rejected.plans[0].status == "approved"
    assert "error" in json.dumps(rejected.steps[-1])
