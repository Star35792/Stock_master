"""Real FastAPI + four stores + services, replacing only the model response."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.routes import agent as route
from app.config.settings import settings
from app.models.strategy import BacktestRun
from app.models.system import JobLog
from app.services import agent_permissions, agent_state, agent_tools
from app.services.agent import AgentService
from app.services.agent_state import AgentStateStore
from app.services.operations_actions import OperationsActionService
from tests.agent_business_support import business_databases, seed_business_inputs
from tests.test_agent_planning import draft, response, step


@pytest.fixture
def business(tmp_path, monkeypatch):
    engines, factories = business_databases(tmp_path)
    monkeypatch.setattr(settings, "factor_reads_curated", False)
    inputs = seed_business_inputs(factories)
    for name, key in (
        ("SessionLocal", "market"),
        ("ResearchSessionLocal", "research"),
        ("PositionsSessionLocal", "positions"),
    ):
        monkeypatch.setattr(agent_tools, name, factories[key])
    store = AgentStateStore(factories["agent"])
    monkeypatch.setattr(agent_permissions, "AgentStateStore", lambda: store)
    monkeypatch.setattr(agent_state, "AgentStateStore", lambda: store)
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    tools = agent_tools.AgentTools()
    tools.operations = OperationsActionService(session_factory=factories["market"])
    model = SimpleNamespace(complete=Mock())
    service = AgentService(store=store, client=model, tools=tools)
    monkeypatch.setattr(route, "service", service)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client, model, service, factories, inputs
    for engine in engines.values():
        engine.dispose()


def post(client, path, payload):
    result = client.post("/api/v1/agent" + path, json=payload)
    assert result.status_code == 200, result.text
    return result.json()


def approve(client, model, steps, text):
    model.complete.return_value = response(draft(steps=steps))
    view = post(client, "/plans", {"message": text})
    p = view["plans"][-1]
    assert p["status"] == "draft", p
    identity = {
        "session_id": view["session_id"],
        "version": p["version"],
        "plan_hash": p["plan_hash"],
    }
    view = post(client, f"/plans/{p['plan_id']}/decision", {**identity, "decision": "approve"})
    return p["plan_id"], identity, view


def test_real_backtest_to_portfolio_checks_results_and_restores_without_replay(business):
    client, model, service, factories, inputs = business
    args = {
        "name": "隔离探索",
        "factor_name": "momentum_20",
        "factor_version": "v1-qfq",
        "higher_is_better": True,
        "initial_cash": "100000",
        "top_n": 2,
        "start_date": inputs["start_date"],
        "end_date": inputs["end_date"],
    }
    text = json.dumps(args)
    plan_id, identity, view = approve(
        client,
        model,
        [
            step(
                "run_factor_backtest",
                "backtest",
                parameters=args,
                input_evidence={key: text for key in args if key != "name"},
            ),
            step(
                "backtest_result",
                "inspect",
                depends_on=["backtest"],
                bindings=[{"parameter": "run_id", "source_step": "backtest", "output": "run_id"}],
            ),
            step(
                "create_portfolio_from_backtest",
                "portfolio",
                parameters={"name": "隔离草案"},
                depends_on=["inspect", "backtest"],
                bindings=[{"parameter": "run_id", "source_step": "backtest", "output": "run_id"}],
            ),
        ],
        text,
    )
    execution_path = f"/plans/{plan_id}/execution"
    with factories["market"]() as db:
        assert db.scalar(select(func.count()).select_from(JobLog)) == 0
    pending = post(client, execution_path, {**identity, "operation": "advance"})
    assert pending["executions"][0]["status"] == "awaiting_confirmation"
    started = post(
        client,
        f"/actions/{pending['actions'][-1]['action_id']}/decision",
        {"session_id": identity["session_id"], "decision": "confirm"},
    )
    first_id = started["actions"][-1]["task_id"]
    assert client.get(f"/api/v1/agent/tasks/{first_id}").json()["status"] == "success"
    pending = post(client, execution_path, {**identity, "operation": "advance"})
    execution = pending["executions"][0]
    assert execution["steps"][0]["summary"]["recovery"]["status"] == "verified"
    assert execution["steps"][1]["status"] == "success"
    assert pending["actions"][-1]["tool"] == "create_portfolio_from_backtest"
    post(
        client,
        f"/actions/{pending['actions'][-1]['action_id']}/decision",
        {"session_id": identity["session_id"], "decision": "confirm"},
    )
    finished = post(client, execution_path, {**identity, "operation": "refresh"})
    assert finished["executions"][0]["status"] == "completed"
    observation = finished["executions"][0]["steps"][-1]["summary"]["observation"]["evidence"]
    assert observation["source"]["kind"] == "portfolio"
    assert observation["facts"]["source_run_id"] > 0
    assert observation["facts"]["execution_allowed"] is False
    assert observation["facts"]["target_count"] > 0
    restored = AgentService(store=service.store, client=model, tools=service.tools)
    saved = restored.get(identity["session_id"])
    assert saved.executions[0].model_dump(mode="json") == finished["executions"][0]
    with factories["market"]() as db, factories["research"]() as research:
        assert db.scalar(select(func.count()).select_from(JobLog)) == 2
        assert research.scalar(select(func.count()).select_from(BacktestRun)) == 1


def test_real_position_analysis_never_writes_ledger_or_creates_jobs(business):
    client, model, _, factories, inputs = business
    text = f"book_id={inputs['book_id']}"
    plan_id, identity, _ = approve(
        client,
        model,
        [
            step(
                "position_holdings",
                "holdings",
                parameters={"book_id": inputs["book_id"]},
                input_evidence={"book_id": text},
            )
        ],
        text,
    )
    view = post(client, f"/plans/{plan_id}/execution", {**identity, "operation": "advance"})
    evidence = view["executions"][0]["steps"][0]["summary"]["evidence"]
    assert evidence["facts"]["holding_count"] == 1
    assert evidence["facts"]["missing_price_count"] == 0
    assert evidence["facts"]["total_equity"]
    with factories["market"]() as db:
        assert db.scalar(select(func.count()).select_from(JobLog)) == 0


def test_real_maintenance_diagnosis_confirmation_execution_and_recheck(business):
    client, model, _, factories, inputs = business
    args = {
        "start_date": inputs["start_date"],
        "end_date": inputs["end_date"],
        "categories": ["price_volume_risk"],
    }
    text = json.dumps(args)
    plan_id, identity, _ = approve(
        client,
        model,
        [
            step("operations_overview", "diagnose"),
            step(
                "rebuild_factor_profiles",
                "maintain",
                parameters=args,
                depends_on=["diagnose"],
                input_evidence={key: text for key in args},
            ),
        ],
        text,
    )
    path = f"/plans/{plan_id}/execution"
    pending = post(client, path, {**identity, "operation": "advance"})
    assert pending["executions"][0]["steps"][0]["status"] == "success"
    assert pending["actions"][-1]["status"] == "pending"
    action_id = pending["actions"][-1]["action_id"]
    decision = {"session_id": identity["session_id"], "decision": "confirm"}
    started = post(client, f"/actions/{action_id}/decision", decision)
    post(client, f"/actions/{action_id}/decision", decision)
    task_id = started["actions"][-1]["task_id"]
    task = client.get(f"/api/v1/agent/tasks/{task_id}").json()
    assert task["status"] == "success", task
    finished = post(client, path, {**identity, "operation": "refresh"})
    assert finished["executions"][0]["status"] == "completed"
    summary = finished["executions"][0]["steps"][-1]["summary"]
    assert summary["recovery"]["status"] == "verified"
    facts = summary["observation"]["evidence"]["facts"]
    assert facts["problem_closed"] is True
    assert facts["profile_rows"] > 0 and facts["failed_count"] == 0
    with factories["market"]() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(JobLog).where(JobLog.job_name == "operator_action")
            )
            == 1
        )
