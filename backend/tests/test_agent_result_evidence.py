import json
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.schemas.agent_execution import AgentStepExecution
from app.schemas.agent_recovery import AgentRecoveryRead
from app.services import agent_tools as tools_module
from app.services.agent import AgentService
from app.services.agent_execution import AgentExecutionService
from app.services.agent_execution_contract import result_summary
from app.services.agent_state import AgentStateStore
from app.services.agent_tools import AgentTools
from tests.test_agent_execution import control, setup
from tests.test_agent_planning import step, storage as storage


def test_nested_backtest_summary_retains_exact_facts_without_business_details():
    raw = {
        "run": {
            "id": 7,
            "status": "success",
            "start_date": "2026-01-05",
            "end_date": "2026-01-09",
            "final_equity": Decimal("100123.1234"),
            "total_return": Decimal("0.0012312340"),
            "data_version": "release-a",
            "code_version": "source-a",
            "parameters": {"training_artifact_verified": False, "secret_payload": "excluded"},
        },
        "performance": {"sharpe_ratio": None, "maximum_drawdown": Decimal("0.0000000000")},
        "final_holdings": {"as_of_date": "2026-01-09", "holdings": [{"symbol": "hidden"}]},
        "equity_curve": [{"private": "hidden"}] * 5000,
    }
    summary = result_summary(raw)
    facts = summary["evidence"]["facts"]
    assert facts["final_equity"] == "100123.1234"
    assert facts["maximum_drawdown"] == "0E-10"
    assert facts["training_artifact_verified"] is False
    assert facts["sharpe_ratio"] is None
    assert facts["evidence_qualified"] is None
    assert "PIT、历史ST/退市、公司行为" in "".join(summary["evidence"]["limitations"])
    assert summary["evidence"]["source"] == {"kind": "backtest", "id": 7}
    assert "基准" in "".join(summary["evidence"]["limitations"])
    encoded = json.dumps(summary)
    assert len(encoded) < 4096 and "hidden" not in encoded and "secret_payload" not in encoded


def test_position_summary_exposes_mixed_dates_missing_prices_and_ledger_anomalies():
    result = result_summary(
        {
            "book_id": 2,
            "cash": Decimal("0.0000"),
            "total_equity": Decimal("1200.0000"),
            "price_as_of": "2026-11-18",
            "price_stale": True,
            "sequence_anomaly": True,
            "holdings": [
                {"price": "12", "price_as_of": "2026-11-18"},
                {"price": "11", "price_as_of": "2026-11-17"},
                {"price": None, "price_as_of": None},
            ],
        }
    )["evidence"]
    facts = result["facts"]
    assert facts["cash"] == "0.0000"
    assert facts["earliest_price_date"] == "2026-11-17"
    assert facts["latest_price_date"] == "2026-11-18"
    assert facts["missing_price_count"] == 1 and facts["holding_count"] == 3
    assert "成本估值" in "".join(result["limitations"])
    assert "流水时序异常" in "".join(result["limitations"])


def test_research_portfolio_retains_source_and_execution_boundary():
    evidence = result_summary(
        {
            "plan_id": 8,
            "source_run_id": 7,
            "input_source": "agent_backtest_prefill",
            "as_of_date": "2026-01-09",
            "execution_allowed": False,
            "targets": [],
        }
    )["evidence"]
    assert evidence["source"] == {"kind": "portfolio", "id": 8}
    assert evidence["facts"]["source_run_id"] == 7
    assert evidence["facts"]["execution_allowed"] is False
    assert "零现仓" in "".join(evidence["limitations"])


@pytest.mark.parametrize("status", ["running", "partial", "failed"])
def test_nested_unsuccessful_run_cannot_look_like_success(status):
    state = AgentStepExecution(step_id="inspect")
    AgentExecutionService._read_state(state, {"run": {"id": 7, "status": status}}, None)
    assert state.status == "blocked"


def test_summary_does_not_persist_upstream_error_bodies():
    evidence = result_summary(
        {
            "run": {"id": 7, "status": "success"},
            "performance_error": "secret SQL/provider payload",
        }
    )["evidence"]
    assert evidence["unavailable_sections"] == ["performance"]
    assert "secret" not in json.dumps(evidence)


def test_explanation_after_restart_receives_saved_observations_not_new_authority(storage):
    store, factory = storage
    service, tools, view = setup(store, [step()])
    tools.read_result = {
        "run": {
            "id": 7,
            "status": "success",
            "start_date": "2026-01-05",
            "final_equity": "123456.7890",
        }
    }
    completed = control(service, view)
    client = SimpleNamespace(complete=Mock(return_value={"content": "按原结果解释"}))
    restarted = AgentService(store=AgentStateStore(factory), client=client, tools=Mock())
    response = restarted.chat("解释已保存结果", completed.session_id)
    prompt = client.complete.call_args.args[0][0]["content"]
    assert '"id": 7' in prompt and "123456.7890" in prompt and "2026-01-05" in prompt
    assert '"goal_verified": false' in prompt and '"status": "success"' in prompt
    assert "observed_at" in prompt and "不构成授权" in prompt
    assert "PIT、历史ST/退市、公司行为" in prompt
    assert response.executions[0].goal_verified is False
    assert not restarted.tools.mock_calls


@pytest.mark.parametrize("read_error", [False, True])
def test_write_postcheck_preserves_verified_identity_and_never_replays(monkeypatch, read_error):
    tools = AgentTools()
    args = {"name": "草案", "run_id": 7}
    confirmation = "confirmed-action-000000001"
    identity = tools.action_identity("create_portfolio_from_backtest", args, confirmation)
    job = SimpleNamespace(
        job_name="agent_action",
        metadata_={
            "tool": identity.tool,
            "parameters": args,
            "confirmation_id": confirmation,
            "parameters_hash": identity.parameters_hash,
            "_agent_action": identity.model_dump(),
        },
    )

    @contextmanager
    def session():
        yield Mock()

    @contextmanager
    def context():
        yield Mock(), Mock(), Mock()

    monkeypatch.setattr(tools_module, "SessionLocal", session)
    tools.context = context
    tools.jobs = SimpleNamespace(get=lambda *_: job)
    tools.start = Mock()
    tools.recover_action = Mock(
        return_value=AgentRecoveryRead(
            status="verified",
            reason="matched",
            confirmation_id=confirmation,
            task_id=71,
            artifact_type="portfolio",
            artifact_id=8,
            artifact_verified=True,
            outputs={"task_id": 71},
        )
    )
    get_plan = (
        Mock(side_effect=RuntimeError("secret provider/SQL"))
        if read_error
        else Mock(
            return_value=SimpleNamespace(
                model_dump=lambda **_: {
                    "plan_id": 8,
                    "targets": [],
                    "input_source": "agent_backtest_prefill",
                    "execution_allowed": False,
                }
            )
        )
    )
    monkeypatch.setattr(tools_module.PortfolioConstructionService, "get_plan", get_plan)
    result = tools.task_for_action(71, confirmation, identity.tool, args)
    assert result["status"] == "success" and result["recovery"]["artifact_id"] == 8
    if read_error:
        assert result["result"]["observation"]["unavailable"] is True
        assert "secret" not in json.dumps(result)
    else:
        assert result["result"]["plan_id"] == 8
        facts = result["result"]["observation"]["evidence"]["facts"]
        assert facts["source_run_id"] == 7 and facts["execution_allowed"] is False
    assert not tools.start.called
