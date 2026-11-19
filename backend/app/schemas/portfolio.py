from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config.settings import settings
from app.portfolio.defaults import (
    PortfolioPolicyDefaults,
)
from app.portfolio.enums import ConstraintEnforcementMode


class PortfolioAssetRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    security_type: Literal["stock", "etf", "index"]
    industry: str = Field(min_length=1, max_length=100)
    price: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    current_quantity: int = Field(ge=0)
    requested_weight: Decimal = Field(ge=0, le=1, max_digits=18, decimal_places=10)
    lot_size: int = Field(default=100, gt=0)
    score: Decimal | None = Field(default=None, max_digits=18, decimal_places=10)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be blank")
        return normalized

    @field_validator("industry")
    @classmethod
    def normalize_industry(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("industry must not be blank")
        return normalized


_POLICY = PortfolioPolicyDefaults.from_settings(settings)


class PortfolioRiskBudgetRequest(BaseModel):
    max_industry_weight: Decimal = Field(
        default=_POLICY.max_industry_weight, gt=0, le=1
    )
    max_style_exposure: Decimal | None = Field(
        default=_POLICY.max_style_exposure, ge=0
    )
    max_tracking_error: Decimal | None = Field(
        default=_POLICY.max_tracking_error, ge=0, le=1
    )
    max_portfolio_volatility: Decimal | None = Field(
        default=_POLICY.max_portfolio_volatility, ge=0, le=1
    )
    benchmark: str | None = Field(default=None, min_length=1, max_length=100)


class StyleFactorMapRequest(BaseModel):
    size: tuple[str, str] | None = None
    value: tuple[str, str] | None = None
    momentum: tuple[str, str] | None = None
    volatility: tuple[str, str] | None = None
    liquidity: tuple[str, str] | None = None
    quality: tuple[str, str] | None = None


class PortfolioConstraintsRequest(BaseModel):
    cash_reserve_rate: Decimal = Field(
        default=_POLICY.cash_reserve_rate, ge=0, lt=1
    )
    max_symbol_weight: Decimal = Field(
        default=_POLICY.max_symbol_weight, gt=0, le=1
    )
    max_industry_weight: Decimal = Field(
        default=_POLICY.max_industry_weight, gt=0, le=1
    )
    security_type_budgets: dict[str, Decimal] = Field(default_factory=dict)
    enforcement_mode: ConstraintEnforcementMode = ConstraintEnforcementMode.PROPORTIONAL_SCALE
    extra_symbol_caps: dict[str, Decimal] = Field(default_factory=dict)
    risk_budget: PortfolioRiskBudgetRequest | None = Field(default=None)
    style_factor_map: StyleFactorMapRequest | None = Field(default=None)
    benchmark_symbol: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("security_type_budgets")
    @classmethod
    def validate_security_type_budgets(
        cls,
        value: dict[str, Decimal],
    ) -> dict[str, Decimal]:
        if any(budget < 0 or budget > 1 for budget in value.values()):
            raise ValueError("security type budgets must be between 0 and 1")
        return value

    @field_validator("extra_symbol_caps")
    @classmethod
    def validate_extra_symbol_caps(
        cls,
        value: dict[str, Decimal],
    ) -> dict[str, Decimal]:
        if any(cap <= 0 or cap > 1 for cap in value.values()):
            raise ValueError("extra symbol caps must be within (0, 1]")
        return value


class PortfolioPlanCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    as_of_date: date
    cash: Decimal = Field(ge=0, max_digits=24, decimal_places=4)
    assets: list[PortfolioAssetRequest] = Field(min_length=1)
    constraints: PortfolioConstraintsRequest = Field(default_factory=PortfolioConstraintsRequest)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_trim_mode_requires_scores(self) -> "PortfolioPlanCreateRequest":
        if (
            self.constraints.enforcement_mode
            is not ConstraintEnforcementMode.SCORE_RANKED_TRIM
        ):
            return self
        missing = sorted(asset.symbol for asset in self.assets if asset.score is None)
        if missing:
            raise ValueError(
                "enforcement_mode score_ranked_trim requires a score on every asset; "
                "missing: " + ", ".join(missing)
            )
        return self


class PortfolioTargetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    security_type: str
    industry: str
    requested_weight: Decimal
    constrained_weight: Decimal
    final_weight: Decimal
    current_quantity: int
    target_quantity: int
    order_quantity: int
    price: Decimal
    lot_size: int
    score: Decimal | None = None
    reasons: list[str]


class StyleExposureRead(BaseModel):
    style_name: str
    portfolio_exposure: Decimal
    benchmark_exposure: Decimal | None
    active_exposure: Decimal | None


class PortfolioStyleRiskRead(BaseModel):
    style_exposures: list[StyleExposureRead]
    tracking_error: Decimal | None
    portfolio_volatility: Decimal | None
    missing_styles: list[str]
    missing_benchmark: bool
    factor_versions: dict[str, str] = Field(default_factory=dict)
    factor_evidence: dict[str, dict[str, int | str]] = Field(default_factory=dict)
    factor_as_of_date: date | None = None
    excluded_styles: list[str] = Field(default_factory=list)
    sample_start_date: date | None = None
    sample_end_date: date | None = None
    return_observation_count: int = 0


class PortfolioPlanRead(BaseModel):
    plan_id: int
    name: str
    as_of_date: date
    status: Literal["draft"] = "draft"
    execution_allowed: Literal[False] = False
    input_source: Literal["operator_provided", "agent_backtest_prefill"] = "operator_provided"
    total_equity: Decimal
    cash_before: Decimal
    target_cash: Decimal
    cash_after: Decimal
    constraints: PortfolioConstraintsRequest
    code_version: str
    created_at: datetime
    targets: list[PortfolioTargetRead]
    risk_budget: PortfolioRiskBudgetRequest | None = None
    style_risk: PortfolioStyleRiskRead | None = None


class PortfolioPlanSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: int = Field(validation_alias="id")
    name: str
    as_of_date: date
    total_equity: Decimal
    code_version: str
    created_at: datetime


class PortfolioReadinessCheck(BaseModel):
    code: str
    scope: Literal["plan", "account", "position", "security", "market"]
    passed: bool
    symbol: str | None = None
    message: str


class PortfolioRiskReadinessRead(BaseModel):
    plan_id: int
    paper_account_id: int
    readiness_stage: Literal["pre_risk"] = "pre_risk"
    ready_for_risk_check: bool
    execution_allowed: Literal[False] = False
    risk_evaluated: Literal[False] = False
    orders_created: Literal[False] = False
    read_only: Literal[True] = True
    access_scope: Literal["local_single_user"] = "local_single_user"
    consistency_level: Literal["read_committed_diagnostic"] = "read_committed_diagnostic"
    checked_at: datetime
    account_snapshot_hash: str
    market_snapshot_hash: str
    checks: list[PortfolioReadinessCheck]
    blockers: list[PortfolioReadinessCheck]


class PortfolioDriftRowRead(BaseModel):
    symbol: str
    industry: str | None = None
    security_type: str | None = None
    in_plan: bool
    price_as_of: date | None
    target_weight: Decimal
    current_weight: Decimal
    drift: Decimal
    direction: Literal["buy", "sell", "hold"]
    current_quantity: int
    target_quantity: int | None
    reasons: list[str] = []


class PortfolioDriftSummaryRead(BaseModel):
    max_abs_drift: Decimal
    off_threshold_count: int
    cash_weight: Decimal
    cash_target_weight: Decimal


class PortfolioPlanDriftRead(BaseModel):
    plan_id: int
    plan_name: str
    plan_as_of_date: date
    plan_total_equity: Decimal
    source: Literal["position_book", "paper_account"]
    source_id: int
    source_name: str | None = None
    threshold: Decimal
    price_as_of: date | None
    total_equity: Decimal
    summary: PortfolioDriftSummaryRead
    rows: list[PortfolioDriftRowRead]


class PortfolioDriftErrorRead(BaseModel):
    """Machine-readable domain error for portfolio drift diagnostics."""

    detail: str
    code: Literal["portfolio_drift_missing_trusted_prices"]
    symbols: list[str]
