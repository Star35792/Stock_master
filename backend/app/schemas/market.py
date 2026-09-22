from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class TradingCalendarRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    exchange: str
    trade_date: date
    is_open: bool
    previous_trade_date: date | None
    next_trade_date: date | None
    is_half_day: bool
    source_name: str | None
    updated_at: datetime


class SecurityLifecycleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    security_type: str
    exchange: str
    list_date: date | None
    delist_date: date | None
    is_active: bool
    as_of_date: date
    lifecycle_status: str
    is_trading_day: bool
    should_have_daily_bar: bool
    reason: str


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
    limit_up: Decimal | None = Field(default=None)
    limit_down: Decimal | None = Field(default=None)
    is_suspended: bool = Field(default=False)
    special_treatment_status: str | None = Field(default=None)
    source_name: str
    source_count: int
    quality_score: Decimal
    quality_status: str
    is_suspect: bool
    updated_at: datetime


class MarketSummaryRead(BaseModel):
    """Latest trusted daily snapshot; this is not a real-time quote."""

    symbol: str
    name: str
    security_type: str
    exchange: str
    currency: str
    industry: str | None
    latest_trade_date: date | None
    latest: DailyBarRead | None
    data_status: Literal["available", "unavailable"]
    data_note: str


class CorporateActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    ex_date: date
    action_type: str
    cash_dividend: Decimal | None
    bonus_share_ratio: Decimal | None
    transfer_share_ratio: Decimal | None
    rights_issue_ratio: Decimal | None
    rights_issue_price: Decimal | None
    split_ratio: Decimal | None
    record_date: date | None
    payment_date: date | None
    source_name: str
    source_event_id: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class AdjustmentFactorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    trade_date: date
    adjust_type: str
    factor: Decimal
    source_name: str
    version: str
    quality_status: str
    created_at: datetime
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
