"""Explicit research assumptions; none of these fields certify source data."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

PositiveAmount = Annotated[
    Decimal, Field(gt=0, le=Decimal("1e18"), max_digits=27, decimal_places=8, allow_inf_nan=False)
]
Rate = Annotated[Decimal, Field(ge=0, le=1, max_digits=12, decimal_places=10, allow_inf_nan=False)]


class ResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PriceValueScenario(ResearchInput):
    cash_flows: list[PositiveAmount] = Field(min_length=1, max_length=10)
    terminal_cash_flow: PositiveAmount
    cost_of_equity: Annotated[
        Decimal, Field(gt=0, le=1, max_digits=12, decimal_places=10, allow_inf_nan=False)
    ]
    terminal_growth: Annotated[
        Decimal, Field(gt=-1, lt=1, max_digits=12, decimal_places=10, allow_inf_nan=False)
    ]

    @model_validator(mode="after")
    def require_finite_terminal_value(self) -> "PriceValueScenario":
        if self.cost_of_equity <= self.terminal_growth:
            raise ValueError("cost_of_equity must exceed terminal_growth")
        return self


class PriceValueScenarios(ResearchInput):
    low: PriceValueScenario
    base: PriceValueScenario
    high: PriceValueScenario


class PriceValueFees(ResearchInput):
    commission_rate: Rate
    min_commission: Annotated[
        Decimal, Field(ge=0, le=1000000, max_digits=15, decimal_places=6, allow_inf_nan=False)
    ]
    transfer_fee_rate: Rate
    exchange_fee_rate: Rate
    slippage_bps: Annotated[
        Decimal, Field(ge=0, le=10000, max_digits=12, decimal_places=6, allow_inf_nan=False)
    ]


class PriceValueRequest(ResearchInput):
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.]+$")
    trade_date: date
    valuation_at: AwareDatetime
    assumptions_available_at: AwareDatetime
    valid_until: AwareDatetime
    assumption_version: str = Field(min_length=1, max_length=100)
    assumptions_note: str = Field(min_length=1, max_length=2000)
    currency: Literal["CNY"] = "CNY"
    company_profile: Literal["stable_non_financial", "financial", "cyclical", "distressed"]
    diluted_shares: PositiveAmount
    scenarios: PriceValueScenarios
    required_margin: Annotated[
        Decimal, Field(gt=0, lt=1, max_digits=12, decimal_places=10, allow_inf_nan=False)
    ]
    quantity: int = Field(gt=0, le=1000000000, strict=True)
    fees: PriceValueFees
    has_position: bool = False
    thesis_broken: bool = False
