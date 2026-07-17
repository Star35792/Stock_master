from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SecurityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    security_type: str
    exchange: str
    currency: str
    list_date: date | None
    delist_date: date | None
    is_active: bool
    industry: str | None
    area: str | None
    board: str | None
    updated_at: datetime


class DailyBarRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    security_type: str
    trade_date: date
    adjust_type: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal | None
    change_amount: Decimal | None
    pct_change: Decimal | None
    volume: Decimal | None
    amount: Decimal | None
    turnover_rate: Decimal | None
    amplitude: Decimal | None
    source_name: str
    source_count: int
    quality_score: Decimal
    quality_status: str
    is_suspect: bool
    updated_at: datetime


class MissingDailyBarRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    security_type: str
    exchange: str
    trade_date: date
    adjust_type: str


class DailyBarSourceCompareRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    trade_date: date
    adjust_type: str
    source_a: str
    source_b: str
    raw_id_a: int
    raw_id_b: int
    close_diff: Decimal | None
    close_diff_pct: Decimal | None
    volume_diff_pct: Decimal | None
    amount_diff_pct: Decimal | None
    is_conflict: bool
    conflict_reason: str | None
    created_at: datetime


class ManualCorrectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset: str
    symbol: str
    trade_date: date
    adjust_type: str
    field_name: str
    old_value: str | None
    new_value: str
    reason: str
    corrected_by: str
    created_at: datetime
    applied_at: datetime | None
