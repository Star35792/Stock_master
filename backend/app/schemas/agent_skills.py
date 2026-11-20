"""Skill identities and server-owned evidence checks, never execution grants."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentSkillIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentSkillVerification(BaseModel):
    skill: AgentSkillIdentity
    status: Literal["ready_to_explain", "needs_evidence", "needs_input"]
    task_check_complete: bool
    goal_verified: Literal[False] = False
    observed_at: float
    missing_checks: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=12)
