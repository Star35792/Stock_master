from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.position_book import (
    PositionConcentrationRead,
    PositionIndustryExposureRead,
)


class PaperPositionRowRead(BaseModel):
    symbol: str
    name: str | None
    industry: str | None
    security_type: str | None
    exchange: str | None
    quantity: int
    average_cost: Decimal
    price: Decimal | None
    price_as_of: date | None
    price_quality_status: str | None
    price_stale: bool
    market_value: Decimal
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: Decimal | None
    weight: Decimal


class PaperPositionsRead(BaseModel):
    account_id: int
    price_as_of: date | None
    price_stale: bool
    cash_balance: Decimal
    positions_market_value: Decimal
    total_equity: Decimal
    concentration: PositionConcentrationRead
    industry_exposure: list[PositionIndustryExposureRead]
    holdings: list[PaperPositionRowRead]


class PaperPerformancePointRead(BaseModel):
    valuation_date: date
    cash_balance: Decimal
    positions_market_value: Decimal
    total_equity: Decimal
    drawdown: Decimal
    history_status: str


class PaperPerformanceSummaryRead(BaseModel):
    observation_count: int
    monitoring_start_date: date | None
    first_valuation_date: date | None
    last_valuation_date: date | None
    latest_total_equity: Decimal
    high_watermark: Decimal
    max_drawdown: Decimal
    cumulative_return: Decimal | None
    annualized_return: Decimal | None
    approximate: bool = False
    daily_volatility: Decimal | None
    volatility_approximate: bool = False


class PaperPerformanceRead(BaseModel):
    account_id: int
    summary: PaperPerformanceSummaryRead
    curve: list[PaperPerformancePointRead]
