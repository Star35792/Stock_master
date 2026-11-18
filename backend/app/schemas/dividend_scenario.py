from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def exact_decimal(value):
    if isinstance(value, (float, bool)):
        raise ValueError("请使用精确十进制字符串，不接受浮点或布尔金额")
    return value


Money = Annotated[
    Decimal,
    BeforeValidator(exact_decimal),
    Field(ge=0, max_digits=20, decimal_places=2, allow_inf_nan=False),
]
Price = Annotated[
    Decimal,
    BeforeValidator(exact_decimal),
    Field(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False),
]
Rate = Annotated[
    Decimal,
    BeforeValidator(exact_decimal),
    Field(ge=0, le=1, max_digits=9, decimal_places=8, allow_inf_nan=False),
]


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DividendEventInput(ScenarioInput):
    event_id: str = Field(min_length=1, max_length=80, pattern=r"^\S+$")
    revision: str = Field(min_length=1, max_length=40, pattern=r"^\S+$")
    action_type: Literal["cash_dividend"] = "cash_dividend"
    record_date: date
    ex_date: date
    payment_date: date
    cash_per_share: Annotated[
        Decimal, BeforeValidator(exact_decimal), Field(ge=0, max_digits=14, decimal_places=6)
    ]
    assumed_tax_rate: Rate


class ScenarioPriceInput(ScenarioInput):
    trade_date: date
    close: Price


class ScenarioFillInput(ScenarioInput):
    trade_date: date
    side: Literal["buy", "sell"]
    quantity: int = Field(ge=1, le=10_000_000, strict=True)
    price: Price
    fee: Money = Decimal(0)


class DividendScenarioRequest(ScenarioInput):
    input_kind: Literal["synthetic"] = "synthetic"
    price_basis: Literal["unadjusted_synthetic"] = "unadjusted_synthetic"
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_.-]+$")
    initial_cash: Money
    initial_quantity: int = Field(ge=0, le=10_000_000, strict=True)
    initial_price: Price
    prices: list[ScenarioPriceInput] = Field(min_length=1, max_length=366)
    events: list[DividendEventInput] = Field(min_length=1, max_length=32)
    fills: list[ScenarioFillInput] = Field(default_factory=list, max_length=500)


class DividendScenarioDay(BaseModel):
    trade_date: date
    cash: Decimal
    quantity: int
    sellable_quantity: int
    close: Decimal
    receivable: Decimal
    market_value: Decimal
    total_equity: Decimal
    cumulative_gross_dividend: Decimal
    cumulative_assumed_tax: Decimal
    cumulative_fees: Decimal


class DividendScenarioEventRead(BaseModel):
    event_id: str
    revision: str
    eligible_quantity: int
    gross: Decimal
    assumed_tax: Decimal
    net: Decimal
    status: Literal["awaiting_record", "awaiting_ex", "receivable", "paid"]


class DividendScenarioRead(BaseModel):
    version: Literal["synthetic_cash_dividend_v1"] = "synthetic_cash_dividend_v1"
    research_only: Literal[True] = True
    execution_eligible: Literal[False] = False
    production_policy: Literal["reject"] = "reject"
    price_basis: Literal["unadjusted_synthetic"] = "unadjusted_synthetic"
    tax_policy: Literal["fixed_assumed_rate_at_ex_v1"] = "fixed_assumed_rate_at_ex_v1"
    rounding_policy: Literal["per_event_and_fill_half_up_cents_v1"] = (
        "per_event_and_fill_half_up_cents_v1"
    )
    symbol: str
    contract_sha256: str
    initial_equity: Decimal
    final_equity: Decimal
    total_return: Decimal
    duplicate_events_ignored: int
    days: list[DividendScenarioDay]
    events: list[DividendScenarioEventRead]
    declarations: list[str]
