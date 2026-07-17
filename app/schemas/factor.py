from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class FactorDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str | None
    description: str | None
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
    top_quantile_turnover: Decimal | None
    code_version: str
    created_at: datetime
    updated_at: datetime
