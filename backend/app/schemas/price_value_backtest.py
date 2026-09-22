from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.price_value import (
    PositiveAmount,
    PriceValueFees,
    PriceValueScenarios,
    Rate,
    ResearchInput,
)


class PriceValueAssumption(ResearchInput):
    version: str = Field(min_length=1, max_length=100)
    valuation_at: AwareDatetime
    available_at: AwareDatetime
    valid_until: AwareDatetime
    source_reference: str = Field(min_length=1, max_length=2000)
    diluted_shares: PositiveAmount
    scenarios: PriceValueScenarios
    thesis_broken: bool = False

    @model_validator(mode="after")
    def validate_times(self):
        if self.valuation_at > self.available_at or self.available_at > self.valid_until:
            raise ValueError("require valuation_at <= available_at <= valid_until")
        return self


class PriceValueBacktestRequest(ResearchInput):
    name: str = Field(min_length=1, max_length=100)
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    start_date: date
    end_date: date
    initial_cash: Annotated[Decimal, Field(gt=0, le=Decimal("1e12"), decimal_places=4)]
    position_weight: Annotated[Decimal, Field(gt=0, le=1, decimal_places=8)]
    required_margin: Annotated[Decimal, Field(gt=0, lt=1, decimal_places=10)]
    max_participation_rate: Annotated[Decimal, Field(gt=0, le=Decimal("0.1"), decimal_places=8)]
    fees: PriceValueFees
    sell_stamp_duty_rate: Rate
    assumptions: list[PriceValueAssumption] = Field(min_length=1, max_length=100)
    company_profile: Literal["stable_non_financial"] = "stable_non_financial"
    currency: Literal["CNY"] = "CNY"
    lot_size: Literal[100] = 100

    @model_validator(mode="after")
    def validate_window_and_versions(self):
        if self.start_date >= self.end_date or (self.end_date - self.start_date).days > 1096:
            raise ValueError("backtest requires start < end and at most 1096 calendar days")
        versions = [item.version for item in self.assumptions]
        timestamps = [item.available_at for item in self.assumptions]
        if len(set(versions)) != len(versions) or len(set(timestamps)) != len(timestamps):
            raise ValueError("assumption versions and available_at must be unique")
        return self


class PriceValueDecision(ResearchInput):
    trade_date: date
    phase: Literal["open", "close"]
    action: str
    reason: str
    assumption_version: str | None = None
    signal_date: date | None = None


class PriceValueBacktestTrade(ResearchInput):
    signal_date: date
    trade_date: date
    symbol: str
    side: str
    quantity: int
    reference_price: Decimal
    fill_price: Decimal
    commission: Decimal
    cash_after: Decimal


class PriceValueBacktestEquity(ResearchInput):
    trade_date: date
    cash: Decimal
    market_value: Decimal
    total_equity: Decimal


class PriceValueBacktestPosition(ResearchInput):
    trade_date: date
    symbol: str
    quantity: int
    close: Decimal
    market_value: Decimal


class PriceValueBacktestResponse(ResearchInput):
    run_id: int
    strategy_name: Literal["price_value_assumption_v1"] = "price_value_assumption_v1"
    research_only: Literal[True] = True
    pit_verified: Literal[False] = False
    execution_eligible: Literal[False] = False
    evaluated_at: datetime
    contract_hash: str
    initial_cash: Decimal
    final_equity: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal
    final_cash: Decimal
    trade_count: int
    assumptions: PriceValueBacktestRequest
    limitations: list[str]
    trades: list[PriceValueBacktestTrade]
    equity_curve: list[PriceValueBacktestEquity]
    positions: list[PriceValueBacktestPosition]
    decisions: list[PriceValueDecision]
