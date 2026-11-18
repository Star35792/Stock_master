from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Axis = Literal[
    "top_n",
    "rebalance_interval",
    "coverage_ratio",
    "minimum_score",
    "invested_weight",
    "commission_rate",
    "slippage_bps",
]


class SensitivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(min_length=2, max_length=64)
    baseline_run_id: int = Field(gt=0, strict=True)
    axes: list[Axis] = Field(min_length=1, max_length=2)
    hypothesis: str = Field(default="", max_length=1000)
    acceptance_note: str = Field(default="", max_length=1000)
    stop_note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def unique_selection(self):
        if len(set(self.run_ids)) != len(self.run_ids) or any(
            type(item) is not int or item <= 0 for item in self.run_ids
        ):
            raise ValueError("运行编号须为互不重复的正整数。")
        if self.baseline_run_id not in self.run_ids:
            raise ValueError("基准运行必须在所选运行中。")
        if len(set(self.axes)) != len(self.axes):
            raise ValueError("扫描轴不得重复。")
        return self


class SensitivityCell(BaseModel):
    candidate: str
    fold: str
    run_ids: list[int]
    axis_values: list[str | None]
    status: Literal["controlled", "exploratory", "missing", "duplicate", "unavailable"]
    reasons: list[str]
    total_return: Decimal | None = None
    maximum_drawdown: Decimal | None = None
    sharpe_ratio: Decimal | None = None
    delta_return: Decimal | None = None
    contract_differences: list[str] = Field(default_factory=list)


class SensitivitySummary(BaseModel):
    candidate: str
    axis_values: list[str | None]
    complete: bool
    controlled_folds: int
    expected_folds: int
    median_return: Decimal | None
    worst_return: Decimal | None
    return_range: Decimal | None


class SensitivityRead(BaseModel):
    version: Literal["selected_run_sensitivity_v1"] = "selected_run_sensitivity_v1"
    generated_at: datetime
    research_only: Literal[True] = True
    ranked: Literal[False] = False
    recording_stage: Literal["retrospective"] = "retrospective"
    request: SensitivityRequest
    contract_sha256: str
    baseline_candidate: str | None
    folds: dict[str, tuple[date, date]]
    candidates: dict[str, list[str | None]]
    cells: list[SensitivityCell]
    summaries: list[SensitivitySummary]
    unclassified_run_ids: list[int]
    unclassified_reasons: dict[int, str]
    declarations: list[str]
