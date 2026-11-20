"""Real FastAPI + four stores + services, replacing only the model response."""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.routes import agent as route
from app.config.settings import settings
from app.data_sources.tencent import TencentQuoteDataSource
from app.models.market import DailyBarSourceCompare, Security, ValuationSnapshot
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.models.strategy import BacktestRun
from app.models.system import JobLog
from app.schemas.operations import OperationsActionStartRequest
from app.services import agent_permissions, agent_state, agent_tools
from app.services.agent import AgentService
from app.services.agent_state import AgentStateStore
from app.services.operations_actions import OperationsActionService
from tests.agent_business_support import business_databases, seed_business_inputs
from tests.test_agent import call
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


@pytest.mark.parametrize(
    ("quote", "expected_status", "observation_status"),
    [
        ({"price": 12.34, "pe_ttm": 0, "pb": 1.5, "is_stale": False}, "success", "captured"),
        ({"price": 12.34, "is_stale": True}, "partial", "vendor_stale"),
        (None, "failed", "no_quote"),
    ],
)
def test_chat_tencent_quote_confirmation_and_raw_evidence(
    business, monkeypatch, tmp_path, quote, expected_status, observation_status
):
    client, model, service, factories, _ = business
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(service.tools.operations, "write_lock_path", tmp_path / "write.lock")
    monkeypatch.setattr(settings, "agent_write_enabled", False)
    monkeypatch.setattr(settings, "agent_data_maintenance_enabled", False)
    monkeypatch.setattr(TencentQuoteDataSource, "has_local_source", staticmethod(lambda: True))
    fetch = Mock(return_value={"sh000905": {"price": 999}, "sz000905": quote})
    monkeypatch.setattr(TencentQuoteDataSource, "fetch_quotes", fetch)
    with factories["market"]() as db:
        db.add(Security(symbol="000905.SZ", exchange="SZ", name="样本股", security_type="stock"))
        db.commit()
        with pytest.raises(ValueError, match="聊天助手确认"):
            service.tools.operations.start(
                db,
                request=OperationsActionStartRequest(
                    action_type="tencent_quote_capture",
                    parameters={"symbol": "000905.SZ"},
                    idempotency_key="direct-quote",
                ),
            )
    text = "请采集腾讯 000905.SZ 的报价"
    model.complete.side_effect = [
        call("submit_research_plan", draft(steps=[step(
            "capture_tencent_quote", "quote",
            parameters={"symbol": "000905.SZ"}, input_evidence={"symbol": text},
        )])),
        {"role": "assistant", "content": "报价采集计划待审阅。"},
    ]
    view = post(client, "/chat", {"message": text})
    plan = view["plans"][-1]
    assert plan["status"] == "draft"
    identity = {
        "session_id": view["session_id"],
        "version": plan["version"],
        "plan_hash": plan["plan_hash"],
    }
    path = f"/plans/{plan['plan_id']}"
    post(client, path + "/decision", {**identity, "decision": "approve"})
    pending = post(client, path + "/execution", {**identity, "operation": "advance"})
    assert not fetch.called
    decision = {"session_id": identity["session_id"], "decision": "confirm"}
    decision_path = f"/actions/{pending['actions'][-1]['action_id']}/decision"
    rejected = client.post("/api/v1/agent" + decision_path, json=decision)
    assert rejected.status_code == 409 and "写执行仍关闭" in rejected.json()["detail"]
    assert not fetch.called
    monkeypatch.setattr(settings, "agent_data_maintenance_enabled", True)
    started = post(client, decision_path, decision)
    post(client, decision_path, decision)
    fetch.assert_called_once_with(["sz000905"])
    task_id = started["actions"][-1]["task_id"]
    task = client.get(f"/api/v1/agent/tasks/{task_id}").json()
    assert task["status"] == expected_status, task
    result = task["result"]["quote_observation"]
    assert result["observation_status"] == observation_status
    assert result.get("price_cny") == ("12.34" if expected_status == "success" else None)
    assert result.get("pe_ttm") is None and result["quote_time_verified"] is False
    finished = post(client, path + "/execution", {**identity, "operation": "refresh"})
    summary = finished["executions"][0]["steps"][0]["summary"]
    facts = summary["observation"]["evidence"]["facts"]
    assert facts["observation_status"] == observation_status
    assert facts.get("raw_snapshot_id") == result.get("raw_snapshot_id")
    assert facts["observed_at"] == result["observed_at"]
    with factories["market"]() as db:
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1
        snapshot = db.scalar(select(RawVendorSnapshot))
        if quote is not None:
            assert snapshot.id == facts["raw_snapshot_id"]
            assert snapshot.raw_payload["quote"]["price"] == "12.34"
        else:
            assert snapshot is None


def test_chat_tdx_reads_only_existing_comparisons_for_symbol_date_and_source(business):
    client, model, _, factories, inputs = business
    symbol = inputs["symbol"]
    trade_date = date.fromisoformat(inputs["end_date"])
    with factories["market"]() as db:
        for index, pair in enumerate((
            ("tdx_official", "other_a"), ("other_a", "tdx_official"), ("other_a", "other_b"),
        )):
            db.add(DailyBarSourceCompare(
                symbol=symbol, trade_date=trade_date, adjust_type="none",
                source_a=pair[0], source_b=pair[1], raw_id_a=1, raw_id_b=2 + index,
                is_conflict=index == 1,
            ))
        db.commit()
    args = {"symbol": symbol, "trade_date": trade_date.isoformat()}
    model.complete.side_effect = [
        call("tdx_daily_comparison", args), {"role": "assistant", "content": "已有两条对账记录。"},
    ]
    view = post(client, "/chat", {"message": f"查通达信 {symbol} {trade_date} 的日线对账"})
    result = view["steps"][-1]["result"]
    assert result["total"] == 2 and result["trade_date"] == args["trade_date"]
    assert [row["is_conflict"] for row in result["items"]] == [True, False]
    with factories["market"]() as db:
        assert db.scalar(select(func.count()).select_from(JobLog)) == 0


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
