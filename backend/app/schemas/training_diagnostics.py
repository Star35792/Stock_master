from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class DailyRankICEvidenceRead(BaseModel):
    trade_date: date
    rank_ic: Decimal | None = None
    common_symbol_count: int = Field(ge=0)
    status: Literal[
        "recorded",
        "no_factor_values",
        "missing_forward_prices",
        "insufficient_pairs",
        "zero_dispersion",
    ]


class TrainingDiagnosticFactorRead(BaseModel):
    factor_name: str
    factor_version: str
    status: Literal["recorded", "insufficient"]
    reason: str | None = None
    observations: int = Field(ge=0)
    hac_pair_count: int = Field(ge=0)
    mean_rank_ic: Decimal | None = None
    hac_standard_error: Decimal | None = None
    z_score: Decimal | None = None
    p_value: Decimal | None = Field(default=None, ge=0, le=1)
    q_value: Decimal | None = Field(default=None, ge=0, le=1)
    reject_null: bool
    rank_ic_series: list[DailyRankICEvidenceRead] = Field(default_factory=list)


class TrainingDiagnosticsRead(BaseModel):
    run_id: int
    training_artifact_id: int | None = None
    artifact_hash: str | None = None
    contract_version: str | None = None
    status: Literal["recorded", "insufficient", "not_recorded", "unsupported"]
    reason: str | None = None
    research_only: bool = True
    selection_unchanged: bool = True
    qualification_unchanged: bool = True
    policy: dict[str, object] = Field(default_factory=dict)
    family: dict[str, object] = Field(default_factory=dict)
    eligible_dates: list[date] = Field(default_factory=list)
    evidence_sha256: str | None = None
    price_snapshot: dict[str, object] = Field(default_factory=dict)
    factors: list[TrainingDiagnosticFactorRead] = Field(default_factory=list)
    declarations: list[str] = Field(default_factory=list)
