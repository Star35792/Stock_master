"""Explicit reconciliation updates checkpoints without replaying business writes."""

from secrets import token_urlsafe

from app.config.settings import settings
from app.schemas.agent_recovery import AgentRecoveryRead
from app.services.agent_errors import AgentConflictError, AgentNotFoundError
from app.utils.agent_identity import parameters_hash


class AgentPlanRecoveryService:
    def __init__(self, owner):
        self.owner = owner

    def recover(self, session_id, plan_id, version, expected_hash, step_id):
        owner = self.owner
        session = owner.conversation(session_id)
        if not session.lock.acquire(blocking=False):
            raise AgentConflictError("会话正在更新，请稍后核验。")
        try:
            owner.reload(session)
            owner.executor._identity(session, plan_id, version, expected_hash, active=False)
            execution = session.executions.get(plan_id)
            if execution is None:
                raise AgentNotFoundError("计划没有原执行记录。")
            state = next((s for s in execution.steps if s.step_id == step_id), None)
            if state is None or not state.action_id:
                raise AgentNotFoundError("步骤没有原写确认记录，请重新规划。")
            if state.status in {"pending", "preflighting", "awaiting_confirmation"} or (
                state.call_token in owner.executor.active_tokens
            ):
                raise AgentConflictError("原调用仍在处理或尚未确认，请刷新核对，不能重复核验。")
            proposal = session.actions.get(state.action_id)
            if proposal is None:
                raise AgentNotFoundError("原确认记录缺失，不能补造授权。")
            action = proposal.view.model_copy(deep=True)
            if (
                action.plan_id != plan_id
                or action.step_id != step_id
                or action.plan_hash != expected_hash
                or action.plan_version != version
                or parameters_hash(action.tool, action.parameters) != action.parameters_hash
                or state.parameters_hash != action.parameters_hash
            ):
                raise AgentConflictError("原确认与计划步骤身份不一致。")
            if execution.observations >= 256:
                raise AgentConflictError("原任务观察已达上限，请到原页面核查。")
            execution.observations += 1
            token = token_urlsafe(16)
            state.call_token = token
            owner.persist(session)
            original_code = proposal.code_version
            original_task = state.task_id or action.task_id
            session.lock.release()
            try:
                report = owner.tools.recover_action(
                    action.tool,
                    action.parameters,
                    action.action_id,
                    code_version=original_code,
                    task_id=original_task,
                )
                report = AgentRecoveryRead.model_validate(report)
                if report.confirmation_id != action.action_id:
                    raise ValueError("原确认身份不匹配。")
            except Exception:
                report = AgentRecoveryRead(
                    status="unknown",
                    confirmation_id=action.action_id,
                    reason="原任务或产物暂不可读取；未重新执行，请稍后核验或到原页面诊断。",
                )
            finally:
                session.lock.acquire()
            owner.reload(session)
            execution, state = owner.executor._state(session, plan_id, step_id)
            if state.call_token != token:
                raise AgentConflictError("核验期间检查点已变化，请重新载入。")
            state.summary = {**state.summary, "recovery": report.model_dump(mode="json")}
            if report.task_id and report.status != "conflict":
                state.task_id = report.task_id
                stored_action = session.actions[action.action_id].view
                stored_action.task_id = report.task_id
                if stored_action.status == "starting":
                    stored_action.status = "confirmed"
            plan = next(p for p in session.plans if p.plan_id == plan_id)
            active = (
                execution.status != "cancelled"
                and plan is session.plans[-1]
                and plan.status == "approved"
                and plan.code_version == settings.app_code_version
                and plan.expires_at > owner.clock()
                and execution.deadline > owner.clock()
                and original_code == settings.app_code_version
            )
            if active and report.status == "verified":
                state.status, state.reason, state.outputs = "success", report.reason, report.outputs
                blocked = next(
                    (
                        s
                        for s in execution.steps
                        if s.status in {"partial", "failed", "unknown", "blocked"}
                    ),
                    None,
                )
                execution.status = (
                    "blocked"
                    if blocked
                    else "completed"
                    if all(s.status == "success" for s in execution.steps)
                    else "ready"
                )
                execution.reason = (
                    blocked.reason
                    if blocked
                    else "已核验原任务；继续后续步骤仍须满足原计划和逐项权限。"
                )
            elif active and report.status == "running":
                state.status, state.reason = "waiting_task", report.reason
                blocked = next(
                    (
                        s
                        for s in execution.steps
                        if s.status in {"partial", "failed", "unknown", "blocked"}
                    ),
                    None,
                )
                execution.status = "blocked" if blocked else "waiting_task"
                execution.reason = blocked.reason if blocked else report.reason
            elif execution.status != "cancelled":
                state.status = (
                    report.status if report.status in {"partial", "failed"} else "unknown"
                )
                state.reason, state.outputs = report.reason, {}
                execution.status = "blocked"
                execution.reason = (
                    report.reason
                    if active
                    else "原证据已记录；计划已失效，不能续授权或启动后续步骤。"
                )
            owner.persist(session)
            return owner.snapshot(session)
        finally:
            session.lock.release()
