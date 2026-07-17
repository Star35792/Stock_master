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
