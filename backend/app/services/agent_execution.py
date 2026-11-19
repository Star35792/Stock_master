"""Bounded plan execution. Business I/O is fenced by CAS; the model cannot dispatch it."""

from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic

from app.config.settings import settings
from app.schemas.agent import AgentActionRead
from app.schemas.agent_execution import AgentPlanExecution, AgentStepExecution
from app.services.agent_errors import AgentConflictError, AgentNotFoundError
from app.services.agent_execution_contract import (
    compact_preview,
    parameters_hash,
    preview_hash,
    result_summary,
)
from app.services.agent_planning import plan_hash
from app.services.agent_tools import MAINTENANCE, TOOLS, json_ready

IN_FLIGHT = {"reading", "preflighting", "starting"}
MAX_DISPATCHES = 32
MAX_ADVANCES = 64


@dataclass
class Proposal:
    view: AgentActionRead
    expires: float
    code_version: str = ""


class AgentExecutionService:
    def __init__(self, owner):
        self.owner = owner
        self.active_tokens = set()

    def _identity(self, session, plan_id, version, expected_hash, *, active=True):
        self.owner.snapshot(session)
        plan = next((p for p in session.plans if p.plan_id == plan_id), None)
        if plan is None:
            raise AgentNotFoundError("计划不存在或不属于此会话。")
        if (
            plan.version != version
            or plan.plan_hash != expected_hash
            or plan.plan_hash != plan_hash(session.session_id, plan)
        ):
            raise AgentConflictError("计划身份已变化，请重新载入。")
        if active and (plan is not session.plans[-1] or plan.status != "approved"):
            raise AgentConflictError("只允许执行当前有效且已批准的计划。")
        return plan

    @staticmethod
    def _state(session, plan_id, step_id):
        execution = session.executions[plan_id]
        return execution, next(s for s in execution.steps if s.step_id == step_id)

    def _args(self, plan, execution, step):
        args = dict(step.parameters)
        states = {s.step_id: s for s in execution.steps}
        if any(states[parent].status != "success" for parent in step.depends_on):
            raise AgentConflictError("依赖尚未成功，不能执行此步骤。")
        for binding in step.bindings:
            value = states[binding.source_step].outputs.get(binding.output)
            if type(value) is not int or value <= 0:
                raise AgentConflictError("上游缺少已核实的标量输出，不能绑定参数。")
            args[binding.parameter] = value
        spec = TOOLS.get(step.tool)
        if spec is None:
            raise AgentConflictError("工具不在允许清单。")
        return spec.arguments.model_validate(args).model_dump(mode="json")

    def _perform(self, session, plan_id, step_id, phase, callback, *, observation=False):
        execution, state = self._state(session, plan_id, step_id)
        if observation:
            if execution.observations >= 256:
                raise AgentConflictError("原任务观察已达到256次上限，请在原任务页面查看。")
            execution.observations += 1
        else:
            if execution.dispatches >= MAX_DISPATCHES or execution.deadline <= self.owner.clock():
                raise AgentConflictError("执行达到调用或时间上限，不能继续。")
            execution.dispatches += 1
        token = token_urlsafe(16)
        if not observation and execution.status != "cancelled":
            execution.status = "running"
        state.status, state.call_token = phase, token
        state.attempts += 1
        self.owner.persist(session)
        self.active_tokens.add(token)
        session.lock.release()
        value, error = None, None
        try:
            value = callback()
        except Exception as exc:
            # Retain no SQL, credentials, source bodies or local paths.
            error = type(exc).__name__
        finally:
            session.lock.acquire()
            self.active_tokens.discard(token)
        self.owner.reload(session)
        execution, state = self._state(session, plan_id, step_id)
        if state.call_token != token:
            raise AgentConflictError("执行检查点已变化，结果保持待核实。")
        return value, error

    def _perform_read(self, session, plan, step, args):
        from app.services.agent_read_recovery import AgentReadRecovery

        return AgentReadRecovery(self).perform(session, plan, step, args)

    def settle_orphaned_calls(self, session, execution):
        """A lost caller is uncertainty, never proof that an action did not run."""
        changed = False
        for state in execution.steps:
            if state.status not in IN_FLIGHT or state.call_token in self.active_tokens:
                continue
            checkpoint = state.read_checkpoint or {}
            if (
                state.status == "reading"
                and not state.action_id
                and checkpoint.get("status") == "retryable"
            ):
                state.status = "waiting_read"
                state.reason = "原查询已明确瞬时失败；可在原次数和期限内继续只读查询。"
            else:
                state.status = "unknown"
                state.reason = "上次调用结果未知；核查原任务或重新规划，系统不会重放。"
                if checkpoint.get("status") == "calling":
                    state.read_checkpoint = {**checkpoint, "status": "unknown"}
            state.call_token = token_urlsafe(16)  # Fence any late result from the old caller.
            if state.status == "unknown" and execution.status != "cancelled":
                execution.status, execution.reason = "blocked", state.reason
            elif state.status == "waiting_read" and execution.status == "running":
                execution.status, execution.reason = "ready", state.reason
            changed = True
        return changed

    @staticmethod
    def invalidate(session, reason):
        for execution in session.executions.values():
            if execution.status not in {"completed", "cancelled"}:
                execution.status, execution.reason = "cancelled", reason
                for state in execution.steps:
                    if state.status in {"pending", "awaiting_confirmation", "waiting_read"}:
                        state.status = "cancelled"
        for action in session.actions.values():
            if action.view.status == "pending" and action.view.plan_id:
                action.view.status = "cancelled"

    def _continue_allowed(self, session, plan_id):
        plan = next(p for p in session.plans if p.plan_id == plan_id)
        execution = session.executions[plan_id]
        self.owner.snapshot(session)
        allowed = (
            plan is session.plans[-1]
            and plan.status == "approved"
            and execution.status != "cancelled"
            and execution.deadline > self.owner.clock()
        )
        if not allowed and execution.status != "cancelled":
            execution.status, execution.reason = (
                "blocked",
                "计划已失效或达到时间上限，不能启动后续步骤。",
            )
        return allowed

    def _publish(self, session, plan, step, args, preview):
        execution, state = self._state(session, plan.plan_id, step.step_id)
        if len(session.actions) >= 8:
            execution.status, state.status = "blocked", "blocked"
            execution.reason = "本会话提案已达到8项上限，请新建会话。"
            return
        expires = min(self.owner.clock() + 600, plan.expires_at, execution.deadline)
        grant = {
            "session_id": session.session_id,
            "plan_id": plan.plan_id,
            "version": plan.version,
            "plan_hash": plan.plan_hash,
            "step_id": step.step_id,
            "parameters_hash": parameters_hash(step.tool, args),
            "code_version": settings.app_code_version,
            "expires_at": expires,
        }
        view = AgentActionRead(
            action_id=token_urlsafe(24),
            tool=step.tool,
            title=TOOLS[step.tool].title,
            parameters=args,
            preview={**preview, "_agent_grant": grant},
            plan_id=plan.plan_id,
            plan_version=plan.version,
            plan_hash=plan.plan_hash,
            step_id=step.step_id,
            parameters_hash=grant["parameters_hash"],
            preview_hash=preview_hash(preview),
            permission_scope="data_maintenance" if step.tool in MAINTENANCE else "research_write",
        )
        session.actions[view.action_id] = Proposal(view, expires, settings.app_code_version)
        state.action_id, state.parameters_hash = view.action_id, view.parameters_hash
        state.status, execution.status = "awaiting_confirmation", "awaiting_confirmation"

    @staticmethod
    def _read_state(state, raw, error):
        raw = json_ready(raw)
        if error or raw is None or not isinstance(raw, (dict, list)):
            state.status, state.reason = "unknown", "读取结果未知或不可用；不自动重试。"
            if (state.read_checkpoint or {}).get("status") in {"failed", "exhausted"}:
                state.status = "failed"
                state.reason = "只读查询未完成；输入或数据错误不重试，瞬时重试用尽时请重新规划。"
            return
        raw = json_ready(raw)
        state.summary = result_summary(raw)
        if isinstance(raw, dict):
            if isinstance(raw.get("run"), dict) and raw["run"].get("status") != "success":
                state.status, state.reason = "blocked", "原回测尚未完整成功，不能作为成功研究结果。"
                return
            status = raw.get("status")
            if raw.get("error") or raw.get("blocked") or raw.get("truncated"):
                state.status, state.reason = "blocked", "原服务报告错误、阻断或不完整结果。"
                return
            if status in {"partial", "failed", "unknown"} or any(
                value for key, value in raw.items() if key.endswith("_error")
            ):
                state.status, state.reason = "blocked", "原服务部分失败或缺少必要子结果。"
                return
            if status in {"queued", "running"}:
                state.status = "waiting_read"
                return
        state.status, state.reason = "success", "步骤调用完成，业务目标待核验。"

    def _poll(self, session, plan_id, state):
        action = session.actions[state.action_id].view
        raw, error = self._perform(
            session,
            plan_id,
            state.step_id,
            "reading",
            lambda: self.owner.tools.task_for_action(
                state.task_id, action.action_id, action.tool, action.parameters
            ),
            observation=True,
        )
        execution, current = self._state(session, plan_id, state.step_id)
        if error or not isinstance(raw, dict):
            current.status, current.reason = "unknown", "任务归属或结果无法核实，禁止重放。"
        else:
            current.summary = result_summary(raw)
            if isinstance(raw.get("recovery"), dict):
                current.summary["recovery"] = raw["recovery"]
            status = raw.get("status")
            if status in {"queued", "running"}:
                current.status = "waiting_task"
            elif status == "success" and isinstance(raw.get("result"), dict):
                current.status = "success"
                current.outputs = {"task_id": current.task_id}
                if action.tool == "run_factor_backtest":
                    run_id = raw["result"].get("run_id")
                    if type(run_id) is int and run_id > 0:
                        current.outputs["run_id"] = run_id
                    else:
                        current.status, current.reason = "unknown", "任务缺少已核实的回测run_id。"
                current.summary.update(result_summary(raw["result"]))
                if isinstance(raw["result"].get("observation"), dict):
                    current.summary["observation"] = raw["result"]["observation"]
            else:
                current.status = status if status in {"partial", "failed"} else "unknown"
                current.reason = "任务未完整成功，下游保持阻断。"
        blocked = next(
            (s for s in execution.steps if s.status in {"unknown", "partial", "failed", "blocked"}),
            None,
        )
        if blocked and execution.status != "cancelled":
            execution.status, execution.reason = "blocked", blocked.reason
        elif execution.status not in {"cancelled", "blocked"}:
            if current.status == "waiting_task":
                execution.status, execution.reason = "waiting_task", "原任务尚未完成。"
            elif all(s.status == "success" for s in execution.steps):
                execution.status, execution.reason = (
                    "completed",
                    "步骤已完成，业务目标与资格待核验。",
                )
            else:
                execution.status, execution.reason = "ready", "原任务已成功，可继续后续步骤。"
        self.owner.persist(session)

    def control(self, session_id, plan_id, version, expected_hash, operation):
        session = self.owner.conversation(session_id)
        if not session.lock.acquire(blocking=False):
            raise AgentConflictError("此会话正在更新，请稍后重试。")
        try:
            self.owner.reload(session)
            plan = self._identity(
                session, plan_id, version, expected_hash, active=operation == "advance"
            )
            execution = session.executions.get(plan_id)
            if operation == "cancel":
                if execution is None:
                    execution = AgentPlanExecution(
                        plan_id=plan_id,
                        version=version,
                        plan_hash=expected_hash,
                        started_at=self.owner.clock(),
                        deadline=plan.expires_at,
                        steps=[AgentStepExecution(step_id=s.step_id) for s in plan.steps],
                    )
                    session.executions[plan_id] = execution
                self._cancel(session, execution)
                self.owner.persist(session)
                return self.owner.snapshot(session)
            if execution is None:
                if operation != "advance":
                    raise AgentConflictError("计划还没有执行实例。")
                execution = AgentPlanExecution(
                    plan_id=plan_id,
                    version=version,
                    plan_hash=expected_hash,
                    started_at=self.owner.clock(),
                    deadline=min(plan.expires_at, self.owner.clock() + 1200),
                    steps=[AgentStepExecution(step_id=s.step_id) for s in plan.steps],
                )
                session.executions[plan_id] = execution
            self.settle_orphaned_calls(session, execution)
            if any(s.status in IN_FLIGHT for s in execution.steps):
                raise AgentConflictError("步骤正在处理或结果待核实，不能重复调用；请刷新查看。")
            if operation == "advance" and execution.advances >= MAX_ADVANCES:
                raise AgentConflictError("执行推进次数达到64次上限。")
            if operation == "advance":
                execution.advances += 1
            self.owner.persist(session)
            for state in list(execution.steps):
                if state.status == "waiting_task":
                    self._poll(session, plan_id, state)
            execution = session.executions[plan_id]
            if operation == "refresh" or execution.status in {"cancelled", "blocked", "completed"}:
                return self.owner.snapshot(session)
            started = monotonic()
            for _ in range(8):
                if not self._continue_allowed(session, plan_id):
                    break
                plan = next(p for p in session.plans if p.plan_id == plan_id)
                execution = session.executions[plan_id]
                states = {s.step_id: s for s in execution.steps}
                if any(
                    s.status in {"waiting_task", "awaiting_confirmation"} for s in states.values()
                ):
                    execution.status = (
                        "waiting_task"
                        if any(s.status == "waiting_task" for s in states.values())
                        else "awaiting_confirmation"
                    )
                    break
                if all(s.status == "success" for s in states.values()):
                    execution.status, execution.reason = (
                        "completed",
                        "步骤已完成，业务目标与资格待核验。",
                    )
                    break
                step = next(
                    (
                        s
                        for s in plan.steps
                        if states[s.step_id].status in {"pending", "waiting_read"}
                        and all(states[p].status == "success" for p in s.depends_on)
                    ),
                    None,
                )
                if step is None:
                    execution.status, execution.reason = (
                        "blocked",
                        "依赖或必要结果未成功，下游阻断。",
                    )
                    break
                try:
                    args = self._args(plan, execution, step)
                except ValueError:
                    execution.status, execution.reason = "blocked", "参数或结果绑定未通过校验。"
                    break
                if TOOLS[step.tool].writes:
                    raw, error = self._perform(
                        session,
                        plan_id,
                        step.step_id,
                        "preflighting",
                        lambda name=step.tool, values=args: self.owner.tools.preview(name, values),
                    )
                    execution, state = self._state(session, plan_id, step.step_id)
                    if not self._continue_allowed(session, plan_id):
                        state.status = "cancelled"
                        break
                    try:
                        preview = compact_preview(raw) if not error else {}
                        if preview.get("executable") is not True:
                            raise ValueError
                        self._publish(session, plan, step, args, preview)
                    except ValueError:
                        state.status, execution.status = "blocked", "blocked"
                        execution.reason = "原业务预检未通过或结果未知，请到原页面核对。"
                    self.owner.persist(session)
                    break
                raw, error = self._perform_read(session, plan, step, args)
                execution, state = self._state(session, plan_id, step.step_id)
                state.parameters_hash = parameters_hash(step.tool, args)
                self._read_state(state, raw, error)
                if execution.status != "cancelled":
                    execution.status = (
                        "ready"
                        if state.status == "success"
                        else "waiting_task"
                        if state.status == "waiting_read"
                        else "blocked"
                    )
                    execution.reason = state.reason
                self.owner.persist(session)
                if state.status != "success" or monotonic() - started >= 15:
                    break
            execution = session.executions[plan_id]
            if execution.status not in {"cancelled", "blocked"} and all(
                s.status == "success" for s in execution.steps
            ):
                execution.status, execution.reason = (
                    "completed",
                    "步骤已完成，业务目标与资格待核验。",
                )
            self.owner.persist(session)
            return self.owner.snapshot(session)
        finally:
            session.lock.release()

    @staticmethod
    def _cancel(session, execution):
        execution.status, execution.reason = (
            "cancelled",
            "后续步骤已停止；已开始任务可能继续，不作回滚。",
        )
        for state in execution.steps:
            if state.status in {"pending", "awaiting_confirmation", "waiting_read"}:
                state.status = "cancelled"
                if state.action_id:
                    session.actions[state.action_id].view.status = "cancelled"

    def decide(self, session_id, action_id, decision):
        session = self.owner.conversation(session_id)
        if not session.lock.acquire(blocking=False):
            raise AgentConflictError("此会话正在更新，请稍后重试。")
        try:
            self.owner.reload(session)
            self.owner.snapshot(session)
            proposal = session.actions.get(action_id)
            if proposal is None:
                raise AgentNotFoundError("提案不存在或不属于此会话。")
            action = proposal.view
            if decision == "cancel":
                if action.status != "pending":
                    raise AgentConflictError("当前提案不能取消；可停止计划后续步骤。")
                action.status = "cancelled"
                if action.plan_id:
                    self._cancel(session, session.executions[action.plan_id])
                self.owner.persist(session)
                return self.owner.snapshot(session), None
            if decision != "confirm":
                raise ValueError("操作决定无效。")
            if not settings.agent_write_enabled:
                raise AgentConflictError("写执行仍关闭；可运行只读计划并查看预检。")
            if not action.plan_id or not action.step_id:
                raise AgentConflictError("历史独立预览没有计划授权，请在批准计划内重新预检。")
            plan = self._identity(session, action.plan_id, action.plan_version, action.plan_hash)
            execution, state = self._state(session, action.plan_id, action.step_id)
            if execution.status == "cancelled" or state.action_id != action_id:
                raise AgentConflictError("计划已停止或提案已替换。")
            if action.status == "confirmed":
                self.owner.persist(session)
                return self.owner.snapshot(session), None
            if action.status != "pending" or state.status != "awaiting_confirmation":
                raise AgentConflictError("提案已失效或创建结果待核实，不能重复执行。")
            step = next(s for s in plan.steps if s.step_id == action.step_id)
            args = self._args(plan, execution, step)
            if (
                parameters_hash(step.tool, args) != action.parameters_hash
                or parameters_hash(action.tool, action.parameters) != action.parameters_hash
                or preview_hash(action.preview) != action.preview_hash
            ):
                raise AgentConflictError("提案参数或预检身份已变化，请重新规划。")
            # An unresolved earlier write cannot be bypassed by another plan.
            if any(a.view.status == "starting" for a in session.actions.values()):
                raise AgentConflictError("已有任务创建结果未知，请先核查原任务。")
            raw, error = self._perform(
                session,
                plan.plan_id,
                step.step_id,
                "preflighting",
                lambda: self.owner.tools.preview(step.tool, args),
            )
            execution, state = self._state(session, plan.plan_id, step.step_id)
            action = session.actions[action_id].view
            if not self._continue_allowed(session, plan.plan_id) or action.status != "pending":
                state.status = "cancelled"
                self.owner.persist(session)
                return self.owner.snapshot(session), None
            try:
                preview = compact_preview(raw) if not error else {}
                if preview.get("executable") is not True:
                    raise ValueError
            except ValueError:
                action.status, state.status, execution.status = "expired", "blocked", "blocked"
                execution.reason = "确认时预检未通过，未创建任务。"
                self.owner.persist(session)
                return self.owner.snapshot(session), None
            if preview_hash(preview) != action.preview_hash:
                action.status = "expired"
                self._publish(session, plan, step, args, preview)
                execution.reason = "预检影响或来源已变化，请审阅并确认新提案。"
                self.owner.persist(session)
                return self.owner.snapshot(session), None
            # _perform persists the starting intent before releasing the lock for start().
            action.status = "starting"
            task_id, error = self._perform(
                session,
                plan.plan_id,
                step.step_id,
                "starting",
                lambda: self.owner.tools.start(step.tool, args, action.preview, action_id),
            )
            execution, state = self._state(session, plan.plan_id, step.step_id)
            action = session.actions[action_id].view
            if error or type(task_id) is not int or task_id <= 0:
                state.status, state.reason = (
                    "unknown",
                    "任务创建结果未知；禁止重复创建，请核查原任务。",
                )
                if execution.status != "cancelled":
                    execution.status, execution.reason = "blocked", state.reason
                self.owner.persist(session)
                return self.owner.snapshot(session), None
            action.status, action.task_id = "confirmed", task_id
            state.status, state.task_id = "waiting_task", task_id
            if execution.status != "cancelled":
                execution.status, execution.reason = "waiting_task", "已创建任务，尚未确认完成。"
            self.owner.persist(session)
            return self.owner.snapshot(session), task_id
        finally:
            session.lock.release()
