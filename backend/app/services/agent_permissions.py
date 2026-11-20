"""Check server-owned plan grants again at the business-adapter boundary."""

from time import time

from app.config.settings import settings
from app.schemas.agent_planning import AgentPlanRead
from app.services.agent_execution_contract import parameters_hash
from app.services.agent_planning import plan_hash
from app.services.agent_skills import SkillRegistry
from app.services.agent_state import AgentStateStore


def validate_grant(grant, tool, parameters, confirmation_id, *, task_id=None, store=None, now=None):
    if not settings.agent_write_enabled or not isinstance(grant, dict):
        raise ValueError("写执行关闭或缺少服务端计划授权。")
    now = time() if now is None else now
    if (
        grant.get("code_version") != settings.app_code_version
        or not isinstance(grant.get("expires_at"), (int, float))
        or grant["expires_at"] <= now
        or grant.get("parameters_hash") != parameters_hash(tool, parameters)
    ):
        raise ValueError("授权代码、期限或参数身份不匹配。")
    saved = (store or AgentStateStore()).load(grant.get("session_id"))
    if not saved:
        raise ValueError("授权来源会话不存在。")
    plans = saved.get("plans", [])
    if not plans or plans[-1].get("plan_id") != grant.get("plan_id"):
        raise ValueError("计划已被替代。")
    plan = AgentPlanRead.model_validate(plans[-1])
    if (
        plan.status != "approved"
        or plan.version != grant.get("version")
        or plan.plan_hash != grant.get("plan_hash")
        or plan.plan_hash != plan_hash(grant["session_id"], plan)
        or plan.code_version != settings.app_code_version
        or plan.expires_at <= now
        or not SkillRegistry().matches(plan.skills)
    ):
        raise ValueError("计划身份或批准不再有效。")
    execution = saved.get("executions", {}).get(plan.plan_id, {})
    if execution.get("status") in {None, "cancelled", "blocked", "completed"}:
        raise ValueError("计划执行已停止。")
    step = next((s for s in plan.steps if s.step_id == grant.get("step_id")), None)
    state = next(
        (s for s in execution.get("steps", []) if s["step_id"] == grant.get("step_id")), {}
    )
    action = saved.get("actions", {}).get(confirmation_id, {}).get("view", {})
    if (
        step is None
        or step.tool != tool
        or state.get("action_id") != confirmation_id
        or state.get("parameters_hash") != grant["parameters_hash"]
        or action.get("parameters_hash") != grant["parameters_hash"]
        or parameters_hash(tool, action.get("parameters", {})) != grant["parameters_hash"]
    ):
        raise ValueError("步骤或确认参数不匹配。")
    expected = "starting" if task_id is None else "confirmed"
    if action.get("status") != expected or (
        task_id is not None and action.get("task_id") != task_id
    ):
        raise ValueError("确认未消费或任务身份不匹配。")
