from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.price_value import PriceValueRequest


class ResearchReason(BaseModel):
    code: str
    message: str


class PriceValueMarketSnapshot(BaseModel):
    symbol: str
    security_type: str
    trade_date: date
    adjust_type: str
    close: Decimal
    source_name: str
    quality_status: str
    is_suspect: bool
    selected_raw_id: int | None
    updated_at: datetime | None
    volume: Decimal | None
    amount: Decimal | None
    is_suspended: bool
    special_treatment_status: str | None
    limit_up: Decimal | None
    limit_down: Decimal | None


class PriceValueSecuritySnapshot(BaseModel):
    symbol: str
    security_type: str
    currency: str
    exchange: str
    industry: str | None
    is_active: bool
    list_date: date | None
    delist_date: date | None


class PriceValueMetrics(BaseModel):
    gross_margin: Decimal
    cost_adjusted_margin: Decimal
    buy_cost_ceiling: Decimal
    max_buy_quote: Decimal
    all_in_price: Decimal
    fee_total: Decimal
    slippage_cost: Decimal
    price_condition_met: bool


class PriceValueResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["blocked", "price_condition_met", "wait_for_price", "review_required"]
    research_only: Literal[True] = True
    pit_verified: Literal[False] = False
    execution_eligible: Literal[False] = False
    new_allocation: Literal["none"] = "none"
    existing_position_action: Literal["unchanged", "review"]
    model_version: str
    code_version: str
    evaluated_at: datetime
    contract_hash: str
    assumptions: PriceValueRequest
    market_snapshot: PriceValueMarketSnapshot | None
    security_snapshot: PriceValueSecuritySnapshot | None
    valuation: dict[str, Decimal] | None
    price_metrics: PriceValueMetrics | None
    blocking_reasons: list[ResearchReason]
    review_reasons: list[ResearchReason]
    limitations: list[ResearchReason]
