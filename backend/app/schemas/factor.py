from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FactorDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str | None
    description: str | None
    category: str
    frequency: str
    inputs: list[str]
    applicable_security_types: list[str]
    window_days: int | None
    version: str
    dependencies: list[str]
    parameters_schema: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FactorRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    definition_id: int
    factor_name: str
    factor_version: str
    parameters: dict[str, Any]
    code_version: str
    release_id: str | None = None
    input_start_date: date | None
    input_end_date: date | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    rows_written: int
    error_message: str | None
    metadata_: dict[str, Any] | None
    created_at: datetime


class FactorValueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    trade_date: date
    factor_name: str
    factor_version: str
    value: Decimal
    run_id: int
    created_at: datetime
    updated_at: datetime


class FactorProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factor_name: str
    factor_version: str
    trade_date: date
    universe_size: int
    observation_count: int
    missing_count: int
    coverage_rate: Decimal
    missing_rate: Decimal
    mean: Decimal | None
    standard_deviation: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    p01: Decimal | None
    p99: Decimal | None
    extreme_count: int
    ic_1d: Decimal | None
    rank_ic_1d: Decimal | None
    ic_5d: Decimal | None = None
    rank_ic_5d: Decimal | None = None
    ic_20d: Decimal | None = None
    rank_ic_20d: Decimal | None = None
    top_quantile_forward_return_1d: Decimal | None = None
    bottom_quantile_forward_return_1d: Decimal | None = None
    long_short_forward_return_1d: Decimal | None = None
    quantile_monotonicity_1d: Decimal | None = None
    top_quantile_turnover: Decimal | None
    code_version: str
    created_at: datetime
    updated_at: datetime


class FactorBatchProfileRunRequest(BaseModel):
    start_date: date
    end_date: date
    adjust_type: Literal["none", "qfq", "hfq"] = "qfq"
    categories: list[str] | None = None
    excluded_categories: list[str] | None = None
    size_factor_name: str | None = "market_cap"
    size_factor_version: str | None = "v1-qfq"
    code_version: str = "local"
    release_id: str | None = None


class FactorProfileItem(BaseModel):
    factor_name: str
    factor_version: str
    category: str
    success: bool
    profile_rows: int = 0
    transformed_rows: int = 0
    run_ids: dict[str, int] = Field(default_factory=dict)
    error_message: str | None = None


class FactorBatchProfileRunResponse(BaseModel):
    start_date: date
    end_date: date
    adjust_type: str
    success_count: int
    failed_count: int
    total_profile_rows: int
    total_transformed_rows: int
    results: list[FactorProfileItem]


class FactorProfileSummaryRead(BaseModel):
    factor_name: str
    factor_version: str
    trade_date_count: int
    first_trade_date: date
    last_trade_date: date
    mean_coverage_rate: Decimal | None = None
    mean_ic_1d: Decimal | None = None
    mean_rank_ic_1d: Decimal | None = None
    mean_ic_5d: Decimal | None = None
    mean_rank_ic_5d: Decimal | None = None
    mean_ic_20d: Decimal | None = None
    mean_rank_ic_20d: Decimal | None = None
    has_ic_evidence: bool = False
