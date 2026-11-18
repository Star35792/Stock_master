from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class SignalAuditDifferenceRead(BaseModel):
    signal_date: date
    symbol: str
    expected: str | None
    actual: str | None


class SignalAuditCheckRead(BaseModel):
    kind: str
    cutoff: date
    compared_values: int
    differences: int
    changed_input_rows: int = 0
    examples: list[SignalAuditDifferenceRead]


class SignalAuditRead(BaseModel):
    run_id: int
    generated_at: datetime
    status: Literal["passed", "failed", "insufficient", "error"] = "insufficient"
    reason: str
    version: str = "signal_prefix_v1"
    scope: Literal["factor_frame_to_signal"] = "factor_frame_to_signal"
    historical_replay_verified: Literal[False] = False
    input_verified: bool = False
    baseline_matches_run: bool = False
    recorded_code_version: str | None = None
    current_code_version: str
    input_sha256: str | None = None
    input_rows: int = 0
    signal_count: int = 0
    evaluations: int = 0
    cutoffs: list[date] = Field(default_factory=list)
    checks: list[SignalAuditCheckRead] = Field(default_factory=list)
    declarations: list[str]
    limits: dict[str, int]
