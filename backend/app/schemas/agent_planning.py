"""Bounded, reviewable plans. These contracts do not grant execution permission."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentResultBinding(PlanningModel):
    parameter: Literal["task_id", "run_id"]
    source_step: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    output: Literal["task_id", "run_id"]


class AgentPlanStep(PlanningModel):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    tool: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=200)
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        max_length=12,
        description="只填已明确的工具参数；缺失参数直接省略，不能填占位文字。",
    )
    input_evidence: dict[str, str] = Field(
        default_factory=dict,
        max_length=12,
        description="键只能是parameters中实际存在的参数名，值为用户原话；不能用intent等说明键。",
    )
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    bindings: list[AgentResultBinding] = Field(default_factory=list, max_length=2)
    completion_condition: str = Field(min_length=1, max_length=300)


class AgentPlanDraft(PlanningModel):
    intent: Literal[
        "data_maintenance",
        "stock_research",
        "position_analysis",
        "portfolio_planning",
        "consultation",
    ]
    goal: str = Field(min_length=1, max_length=400)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    data_sources: list[str] = Field(min_length=1, max_length=8)
    completion_condition: str = Field(min_length=1, max_length=400)
    clarification_questions: list[str] = Field(default_factory=list, max_length=8)
    steps: list[AgentPlanStep] = Field(default_factory=list, max_length=8)


class AgentPlanRead(AgentPlanDraft):
    # Up to eight model assumptions plus one server-owned default note per step.
    assumptions: list[str] = Field(default_factory=list, max_length=16)
    plan_id: str
    version: int
    plan_hash: str
    code_version: str
    created_at: float
    expires_at: float
    status: Literal["needs_input", "draft", "approved", "rejected", "superseded", "expired"]
    missing_inputs: list[str] = Field(default_factory=list)
    approved_at: float | None = None


class AgentPlanDecision(PlanningModel):
    session_id: str = Field(min_length=24, max_length=100)
    version: int = Field(ge=1)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "reject"]
