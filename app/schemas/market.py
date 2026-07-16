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
