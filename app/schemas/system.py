from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DataQualityIssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset: str
    symbol: str | None
    trade_date: date | None
    adjust_type: str | None
    source_name: str | None
    raw_id: int | None
    issue_type: str
    severity: str
    description: str
    expected_value: dict[str, Any] | None
    actual_value: dict[str, Any] | None
    status: str
    resolved_by: str | None
    resolved_at: datetime | None
    created_at: datetime


class DataQualityIssueStatusUpdate(BaseModel):
    status: str
    resolved_by: str | None = None
