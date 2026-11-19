"""Server-owned execution checkpoints, separate from immutable plan contents."""

from typing import Any, Literal

from pydantic import Field

from app.schemas.agent_planning import PlanningModel


class AgentPlanControl(PlanningModel):
    session_id: str = Field(min_length=24, max_length=100)
    version: int = Field(ge=1)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["advance", "refresh", "cancel"]


class AgentStepExecution(PlanningModel):
    step_id: str
    status: Literal[
        "pending",
        "reading",
        "preflighting",
        "awaiting_confirmation",
        "starting",
        "waiting_task",
        "waiting_read",
        "success",
        "partial",
        "failed",
        "unknown",
        "blocked",
        "cancelled",
    ] = "pending"
    call_token: str | None = None
    action_id: str | None = None
    task_id: int | None = None
    parameters_hash: str | None = None
    outputs: dict[str, int] = Field(default_factory=dict, max_length=2)
    summary: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    attempts: int = 0
    read_checkpoint: dict[str, Any] | None = None


class AgentPlanExecution(PlanningModel):
    plan_id: str
    version: int
    plan_hash: str
    status: Literal[
        "ready",
        "running",
        "awaiting_confirmation",
        "waiting_task",
        "completed",
        "blocked",
        "cancelled",
    ] = "ready"
    started_at: float
    deadline: float
    advances: int = 0
    dispatches: int = 0
    observations: int = 0
    steps: list[AgentStepExecution] = Field(max_length=8)
    reason: str = ""
    goal_verified: Literal[False] = False
