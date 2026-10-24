from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PositionBookCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cash: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = Field(default=None, max_length=200)


class PositionBookUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    cash: Decimal | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=200)


class PositionBookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cash: Decimal
    note: str | None
    created_at: datetime
    updated_at: datetime


class PositionTradeCreateRequest(BaseModel):
    side: Literal["buy", "sell"]
    symbol: str = Field(min_length=1, max_length=20)
    quantity: int = Field(gt=0)
    price: Decimal = Field(gt=0)
    fee: Decimal = Field(default=Decimal("0"), ge=0)
    trade_date: date
    note: str | None = Field(default=None, max_length=500)


class PositionTradeRead(BaseModel):
    id: int
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    price: Decimal
    fee: Decimal
    trade_date: date
    note: str | None
    created_at: datetime


class PositionConcentrationRead(BaseModel):
    position_count: int
    hhi: Decimal
    top5_weight: Decimal
    max_symbol_weight: Decimal


class PositionIndustryExposureRead(BaseModel):
    industry: str
    market_value: Decimal
    weight: Decimal
    symbol_count: int


class PositionHoldingRead(BaseModel):
    symbol: str
    name: str | None
    industry: str | None
    security_type: str | None
    exchange: str | None
    quantity: int
    average_cost: Decimal
    cost_basis: Decimal
    price: Decimal | None
    price_as_of: date | None
    price_quality_status: str | None
    price_stale: bool
    sequence_anomaly: bool = False
    market_value: Decimal
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: Decimal | None
    realized_pnl: Decimal
    weight: Decimal


class PositionBookHoldingsRead(BaseModel):
    book_id: int
    book_name: str
    price_as_of: date | None
    price_stale: bool
    sequence_anomaly: bool = False
    cash: Decimal
    positions_market_value: Decimal
    total_equity: Decimal
    realized_pnl: Decimal
    concentration: PositionConcentrationRead
    industry_exposure: list[PositionIndustryExposureRead]
    holdings: list[PositionHoldingRead]
