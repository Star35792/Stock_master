"""Server-owned recovery evidence; none of these records grants permission."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentActionIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["agent_action_v1"] = "agent_action_v1"
    confirmation_id: str = Field(min_length=24, max_length=100)
    tool: str = Field(min_length=1, max_length=80)
    parameters_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_version: str = Field(min_length=1, max_length=100)


class AgentRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=24, max_length=100)
    version: int = Field(ge=1)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")


class AgentReadCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempts: int = Field(default=0, ge=0, le=3)
    deadline: float = Field(ge=0, allow_inf_nan=False)
    next_retry_at: float = Field(default=0, ge=0, allow_inf_nan=False)
    status: Literal[
        "ready", "calling", "retryable", "success", "failed", "exhausted", "unknown", "cancelled"
    ] = "ready"
    error_kind: (
        Literal["network", "rate_limit", "upstream", "temporary_lock", "permanent"] | None
    ) = None


class AgentRecoveryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["verified", "running", "partial", "failed", "unknown", "conflict"]
    reason: str
    confirmation_id: str
    task_id: int | None = None
    artifact_type: Literal["backtest", "portfolio"] | None = None
    artifact_id: int | None = None
    artifact_status: str | None = None
    artifact_verified: bool = False
    outputs: dict[str, int] = Field(default_factory=dict)
    replayed: Literal[False] = False
    evidence_qualified: Literal[False] = False
