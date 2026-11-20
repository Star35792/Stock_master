"""Skill loading and bounded evidence checks, including real isolated business reads."""

import json
from hashlib import sha256
from shutil import copytree
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.config.settings import settings
from app.schemas.agent_skills import AgentSkillVerification
from app.services.agent import AgentConflictError, AgentService
from app.services.agent_permissions import validate_grant
from app.services.agent_planning import plan_hash
from app.services.agent_skill_verification import SkillTurn
from app.services.agent_skills import SKILL_ROOT, SkillContext, SkillRegistry
from tests.test_agent import FakeTools, MemoryStore, call, service
from tests.test_agent_business_workflow import (
    business as business,  # noqa: F401
    post,
)
from tests.test_agent_planning import draft, response, step


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / "skills"
    copytree(SKILL_ROOT, root)
    return SkillRegistry(root)


def quality_overview():
    return {
        "generated_at": "2026-11-20T03:00:00Z",
        "data_quality": {
            "issue_status_counts": {"open": 1, "reopened": 2},
            "open_issue_severity_counts": {"error": 3},
        },
    }


def test_discovery_and_references_are_progressive_and_hashed(registry):
    catalog = registry.catalog()
    assert len(catalog) == 3
    assert all(set(item) == {"name", "description"} for item in catalog)
    context = SkillContext(registry, "普通问题")
    assert not context.loaded
    assert "费用和研究合同均引用" not in context.prompt()
    result = context.dispatch("load_skill", {"name": "factor-backtest-review"})
    assert "references/evidence.md" in result["references"]
    assert "content" not in result
    reference = context.dispatch(
        "read_skill_reference",
        {
            "name": "factor-backtest-review",
            "resource": "references/evidence.md",
        },
    )
    assert "超额收益" in reference["content"]
    identity = context.identities[0]
    path = registry.root / "factor-backtest-review/references/evidence.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n变化", encoding="utf-8")
    assert not registry.matches([identity])
    with pytest.raises(ValueError, match="当前版本"):
        context.dispatch(
            "read_skill_reference",
            {
                "name": identity.name,
                "resource": "references/evidence.md",
            },
        )


@pytest.mark.parametrize("resource", ["../.env", "C:/secret.md", "references/../../.env"])
def test_reference_escape_is_rejected(registry, resource):
    context = SkillContext(registry, "/factor-backtest-review run_id=7")
    with pytest.raises(ValueError, match="清单"):
        context.dispatch(
            "read_skill_reference",
            {
                "name": "factor-backtest-review",
                "resource": resource,
            },
        )


def test_executable_skill_package_is_rejected(registry):
    path = registry.root / "data-quality-diagnosis/scripts"
    path.mkdir()
    (path / "run.py").write_text("raise RuntimeError('must not run')", encoding="utf-8")
    with pytest.raises(ValueError, match="首版"):
        registry.load("data-quality-diagnosis")


def test_symlink_and_junction_are_rejected(registry, tmp_path):
    path = registry.root / "factor-backtest-review/references/escape.md"
    target = tmp_path / "secret.md"
    target.write_text("secret", encoding="utf-8")
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("本机未开放创建符号链接；目录联接另由Windows验收覆盖")
    with pytest.raises(ValueError, match="链接"):
        registry.load("factor-backtest-review")


def test_chat_fills_missing_evidence_and_persists_no_skill_body():
    agent, tools, _ = service(
        call("operations_overview"),
        call("verify_skill", {"name": "data-quality-diagnosis"}),
        call("quality_issues"),
        call("verify_skill", {"name": "data-quality-diagnosis"}),
        {"content": "已诊断样本，仍有reopened问题；不代表全量认证。"},
    )
    tools.read = Mock(
        side_effect=[
            quality_overview(),
            {
                "items": [{"id": 91}],
                "total": 20,
                "limit": 10,
                "offset": 0,
            },
        ]
    )
    view = agent.chat("/data-quality-diagnosis 检查数据")
    first = view.steps[1]["result"]
    assert first["missing_checks"] == ["quality_issues"]
    check = view.skill_checks[-1]
    assert check.task_check_complete and not check.goal_verified
    page = check.evidence_refs[-1]
    assert page["total"] == 20 and page["returned"] == 1 and page["status_scope"] == "open"
    assert any("reopened" in note for note in check.limitations)
    saved = agent.store.rows[view.session_id]
    assert "发现问题本身" not in json.dumps(saved, ensure_ascii=False)
    restored = AgentService(store=agent.store, tools=tools).get(view.session_id)
    assert restored.skill_checks == view.skill_checks
    assert not tools.starts


def test_implicit_skill_selection_loads_body_only_for_current_model_turn():
    agent, tools, _ = service(
        call("load_skill", {"name": "data-quality-diagnosis"}),
        {"content": "材料不足"},
        call("verify_skill", {"name": "data-quality-diagnosis"}),
        {"content": "缺少概览和质量查询结果。"},
    )
    view = agent.chat("帮我诊断数据质量")
    request = agent.client.complete.call_args_list[1].args[0]
    assert any("发现问题本身" in row["content"] for row in request)
    assert "发现问题本身" not in json.dumps(agent.store.rows, ensure_ascii=False)
    assert view.skill_checks[-1].status == "needs_evidence"
    assert not tools.reads and not tools.starts


def test_skill_reads_are_deduplicated_and_cannot_create_write_previews(monkeypatch):
    monkeypatch.setattr("app.config.settings.settings.agent_max_steps", 4)
    agent, tools, _ = service(
        call("operations_overview"),
        call("operations_overview"),
        call("sync_daily_data"),
        {"content": "需新计划"},
    )
    tools.read = Mock(return_value=quality_overview())
    view = agent.chat("/data-quality-diagnosis 检查")
    assert tools.read.call_count == 1
    assert not tools.previews and not tools.starts
    assert not view.skill_checks[-1].task_check_complete


def test_model_cannot_supply_verdict_or_unrequested_resource():
    turn = SkillTurn(
        SkillRegistry(), "/position-risk-review 检查book_id=7", ["book_id=7"], lambda: 1
    )
    with pytest.raises(ValueError):
        turn.verify({"name": "position-risk-review", "goal_verified": True})
    with pytest.raises(ValueError, match="用户明确"):
        turn.read_key("position_holdings", {"book_id": 8})
    assert turn.finish()[0].status == "needs_input"


def test_empty_skill_plans_keep_legacy_hash():
    agent, _, _ = service(response())
    view = agent.plan("检查")
    plan = view.plans[-1]
    payload = plan.model_dump(mode="json", exclude={"plan_hash", "status", "approved_at", "skills"})
    payload["session_id"] = view.session_id
    legacy = sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    assert plan_hash(view.session_id, plan) == legacy


def test_plan_freezes_loaded_skill_and_changed_reference_expires_approval(registry):
    client = SimpleNamespace(complete=Mock(return_value=response()))
    agent = AgentService(client=client, tools=Mock(), store=MemoryStore(), skills=registry)
    view = agent.plan("/data-quality-diagnosis 检查质量")
    plan = view.plans[-1]
    assert plan.skills[0] == registry.load("data-quality-diagnosis").identity
    assert not agent.tools.mock_calls
    path = registry.root / "data-quality-diagnosis/SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n变化", encoding="utf-8")
    with pytest.raises(AgentConflictError):
        agent.decide_plan(view.session_id, plan.plan_id, plan.version, plan.plan_hash, "approve")
    assert agent.get(view.session_id).plans[-1].status == "expired"


def test_planner_can_select_skill_without_business_calls():
    agent, tools, _ = service(
        call("load_skill", {"name": "position-risk-review"}),
        response(
            draft(
                intent="position_analysis",
                clarification_questions=["请提供book_id"],
                steps=[step("position_holdings")],
            )
        ),
    )
    view = agent.plan("检查持仓风险")
    assert view.plans[-1].status == "needs_input"
    assert view.plans[-1].skills[0].name == "position-risk-review"
    assert not tools.reads and not tools.previews and not tools.starts


def test_real_position_and_quality_checks_through_fastapi(business):
    client, model, service, _, inputs = business
    for skill, responses, text in (
        (
            "position-risk-review",
            [
                call("position_holdings", {"book_id": inputs["book_id"]}),
                call(
                    "verify_skill",
                    {"name": "position-risk-review", "resource_id": inputs["book_id"]},
                ),
                {"content": "按原持仓结果说明风险与限制"},
            ],
            f"book_id={inputs['book_id']}",
        ),
        (
            "data-quality-diagnosis",
            [
                call("operations_overview"),
                call("quality_issues"),
                call("verify_skill", {"name": "data-quality-diagnosis"}),
                {"content": "按原质量统计说明范围"},
            ],
            "检查数据",
        ),
    ):
        model.complete.side_effect = responses
        view = post(client, "/chat", {"message": f"/{skill} {text}"})
        report = AgentSkillVerification.model_validate(view["skill_checks"][-1])
        assert report.task_check_complete, report
        assert not report.goal_verified
        assert report.evidence_refs


def test_plan_execution_generates_checks_and_keeps_goal_unverified(business):
    client, model, service, _, inputs = business
    message = f"/position-risk-review 检查book_id={inputs['book_id']}"
    model.complete.return_value = response(
        draft(
            intent="position_analysis",
            steps=[
                step(
                    "position_holdings",
                    parameters={
                        "book_id": inputs["book_id"],
                    },
                    input_evidence={"book_id": message},
                )
            ],
        )
    )
    view = post(client, "/plans", {"message": message})
    plan = view["plans"][-1]
    identity = {
        "session_id": view["session_id"],
        "version": plan["version"],
        "plan_hash": plan["plan_hash"],
    }
    post(client, f"/plans/{plan['plan_id']}/decision", {**identity, "decision": "approve"})
    view = post(client, f"/plans/{plan['plan_id']}/execution", {**identity, "operation": "advance"})
    execution = view["executions"][-1]
    assert execution["status"] == "completed"
    assert execution["skill_checks"][0]["task_check_complete"]
    assert execution["goal_verified"] is False
    restored = AgentService(store=service.store).get(view["session_id"])
    assert restored.executions[-1].skill_checks[0].task_check_complete


def test_changed_skill_blocks_pending_confirmation_and_background_grant(registry, monkeypatch):
    monkeypatch.setattr(settings, "agent_write_enabled", True)
    payload = draft(steps=[step("sync_daily_data")])
    tools = FakeTools()
    agent = AgentService(
        client=SimpleNamespace(complete=Mock(return_value=response(payload))),
        tools=tools,
        store=MemoryStore(),
        skills=registry,
        clock=lambda: 1000,
    )
    view = agent.plan("/data-quality-diagnosis 同步日线")
    plan = view.plans[-1]
    agent.decide_plan(view.session_id, plan.plan_id, plan.version, plan.plan_hash, "approve")
    view = agent.executor.control(
        view.session_id, plan.plan_id, plan.version, plan.plan_hash, "advance"
    )
    action = view.actions[-1]
    saved = agent.store.rows[view.session_id]
    saved["actions"][action.action_id]["view"]["status"] = "starting"
    grant = {
        "session_id": view.session_id,
        "plan_id": plan.plan_id,
        "version": plan.version,
        "plan_hash": plan.plan_hash,
        "code_version": settings.app_code_version,
        "expires_at": 1600,
        "step_id": "inspect",
        "parameters_hash": action.parameters_hash,
    }
    from unittest.mock import patch

    with patch("app.services.agent_permissions.SkillRegistry", return_value=registry):
        validate_grant(grant, "sync_daily_data", {}, action.action_id, store=agent.store, now=1000)
        path = registry.root / "data-quality-diagnosis/SKILL.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n变化", encoding="utf-8")
        with pytest.raises(ValueError, match="批准"):
            validate_grant(
                grant, "sync_daily_data", {}, action.action_id, store=agent.store, now=1000
            )
    saved["actions"][action.action_id]["view"]["status"] = "pending"
    agent.reload(agent.sessions[view.session_id])
    with pytest.raises(AgentConflictError):
        agent.decide(view.session_id, action.action_id, "confirm")
    assert not tools.starts


def test_reads_before_skill_load_cannot_be_used_as_evidence():
    turn = SkillTurn(SkillRegistry(), "普通需求", ["book_id=7"], lambda: 1)
    turn.observe("operations_overview", {}, quality_overview())
    turn.dispatch("load_skill", {"name": "data-quality-diagnosis"})
    assert turn.verify({"name": "data-quality-diagnosis"}).missing_checks == [
        "operations_overview",
        "quality_issues",
    ]


def test_multi_resource_and_check_limit_cannot_produce_false_completion():
    turn = SkillTurn(
        SkillRegistry(), "/data-quality-diagnosis 检查", ["600000.SH 600001.SH"], lambda: 1
    )
    for symbol in ("600000.SH", "600001.SH"):
        turn.observe(
            "quality_issues",
            {"symbol": symbol},
            {
                "items": [],
                "total": 0,
                "limit": 10,
                "offset": 0,
            },
        )
    turn.observe("operations_overview", {}, quality_overview())
    for _ in range(4):
        assert not turn.verify({"name": "data-quality-diagnosis"}).task_check_complete
    with pytest.raises(ValueError, match="上限"):
        turn.verify({"name": "data-quality-diagnosis"})
    turn.observe("operations_overview", {}, quality_overview())
    assert not turn.finish()[0].task_check_complete


def test_legal_long_skill_is_complete_in_transient_context(registry):
    path = registry.root / "data-quality-diagnosis/SKILL.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n" + "a" * 7000 + "END_RULE", encoding="utf-8"
    )
    agent, _, _ = service(
        call("load_skill", {"name": "data-quality-diagnosis"}),
        call("verify_skill", {"name": "data-quality-diagnosis"}),
        {"content": "缺证据"},
    )
    agent.skills = registry
    agent.chat("检查数据")
    request = agent.client.complete.call_args_list[1].args[0]
    assert any("END_RULE" in item["content"] for item in request)
    assert "END_RULE" not in json.dumps(agent.store.rows)


def test_missing_backtest_and_position_material_stays_incomplete():
    turn = SkillTurn(SkillRegistry(), "/factor-backtest-review run_id=7", ["run_id=7"], lambda: 1)
    turn.observe(
        "backtest_result",
        {"run_id": 7},
        {
            "run": {
                "id": 7,
                "status": "success",
                "start_date": "2026-01-01",
                "end_date": "2026-03-01",
                "code_version": "v1",
                "data_version": "v1",
                "parameters": {},
            },
            "performance": None,
        },
    )
    report = turn.verify({"name": "factor-backtest-review", "resource_id": 7})
    assert report.status == "needs_evidence"
    assert set(report.missing_checks) == {"原绩效指标", "原费用合同"}
    turn.observe(
        "backtest_result",
        {"run_id": 7},
        {
            "run": {
                "id": 7,
                "status": "success",
                "start_date": "2026-01-01",
                "end_date": "2026-03-01",
                "code_version": "v1",
                "data_version": "v1",
                "fee_model_version": "original-fees",
                "parameters": {},
            },
            "performance": {"total_return": "0.010000"},
        },
    )
    report = turn.verify({"name": "factor-backtest-review", "resource_id": 7})
    assert report.task_check_complete
    assert report.evidence_refs[0]["fee_model_version"] == "original-fees"


def test_unknown_explicit_skill_is_a_safe_client_error():
    agent, tools, _ = service()
    with pytest.raises(ValueError, match="发布包"):
        agent.chat("/unknown-skill 请检查")
    assert not tools.reads and not tools.starts
