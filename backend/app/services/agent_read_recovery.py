"""CAS adapter for the approved-plan executor's read hook."""

from app.config.settings import settings
from app.schemas.agent_recovery import AgentReadCheckpoint
from app.services.agent_recovery import ReadRecoveryPolicy, ReadRecoveryStopped
from app.utils.agent_identity import parameters_hash


class AgentReadRecovery:
    def __init__(self, executor, *, wait=None):
        self.executor = executor
        options = {"wait": wait} if wait is not None else {}
        self.policy = ReadRecoveryPolicy(clock=executor.owner.clock, **options)

    def perform(self, session, plan, step, args):
        from app.services.agent_execution import MAX_DISPATCHES

        executor, owner = self.executor, self.executor.owner
        execution, state = executor._state(session, plan.plan_id, step.step_id)
        operation_hash = parameters_hash(step.tool, args)
        checkpoint = (
            AgentReadCheckpoint.model_validate(state.read_checkpoint)
            if state.read_checkpoint is not None
            else AgentReadCheckpoint(
                operation_hash=operation_hash,
                deadline=min(execution.deadline, owner.clock() + 30),
            )
        )
        # A known running result may be explicitly polled again within the same
        # bounded step; a lost result (`calling`/`unknown`) may not be replayed.
        if state.status == "waiting_read" and checkpoint.status == "success":
            checkpoint.status = "ready"
        state.read_checkpoint = checkpoint.model_dump(mode="json")

        def callback():
            token = executor._state(session, plan.plan_id, step.step_id)[1].call_token

            def cancelled():
                with session.lock:
                    owner.reload(session)
                    current, row = executor._state(session, plan.plan_id, step.step_id)
                    return current.status == "cancelled" or row.call_token != token

            def save(value):
                with session.lock:
                    owner.reload(session)
                    current, row = executor._state(session, plan.plan_id, step.step_id)
                    if row.call_token != token:
                        raise ReadRecoveryStopped("查询检查点已变化，不能继续调用。")
                    current_plan = next(p for p in session.plans if p.plan_id == plan.plan_id)
                    if value.status == "calling" and (
                        current.status == "cancelled"
                        or current_plan.status != "approved"
                        or current_plan.code_version != settings.app_code_version
                        or current_plan.expires_at <= owner.clock()
                    ):
                        value.status = "cancelled"
                        row.read_checkpoint = value.model_dump(mode="json")
                        owner.persist(session)
                        raise ReadRecoveryStopped("计划已停止或失效，未启动后续查询。")
                    previous = row.read_checkpoint or {}
                    new_retry = (
                        value.status == "calling"
                        and value.attempts > checkpoint.attempts + 1
                        and value.attempts > previous.get("attempts", 0)
                    )
                    if new_retry:
                        if current.dispatches >= MAX_DISPATCHES:
                            raise ReadRecoveryStopped("计划调用次数已达上限。")
                        current.dispatches += 1
                    row.read_checkpoint = value.model_dump(mode="json")
                    row.attempts = value.attempts
                    owner.persist(session)

            return self.policy.run(
                checkpoint,
                operation_hash=operation_hash,
                read=lambda: owner.tools.read(step.tool, args),
                save=save,
                cancelled=cancelled,
            )

        return executor._perform(session, plan.plan_id, step.step_id, "reading", callback)
