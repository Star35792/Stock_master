from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.portfolio.enums import ConstraintEnforcementMode


class PortfolioAssetRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    security_type: Literal["stock", "etf", "index"]
    industry: str = Field(min_length=1, max_length=100)
    price: Decimal = Field(gt=0)
    current_quantity: int = Field(ge=0)
    requested_weight: Decimal = Field(ge=0, le=1)
    lot_size: int = Field(default=100, gt=0)
    score: Decimal | None = None

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


class PortfolioRiskBudgetRequest(BaseModel):
    max_industry_weight: Decimal = Field(default=Decimal("0.40"), gt=0, le=1)
    max_style_exposure: Decimal | None = Field(default=None, ge=0, le=1)
    max_tracking_error: Decimal | None = Field(default=None, ge=0, le=1)
    max_portfolio_volatility: Decimal | None = Field(default=None, ge=0, le=1)
    benchmark: str | None = Field(default=None, min_length=1, max_length=100)


class PortfolioConstraintsRequest(BaseModel):
    cash_reserve_rate: Decimal = Field(default=Decimal("0.05"), ge=0, lt=1)
    max_symbol_weight: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    max_industry_weight: Decimal = Field(default=Decimal("0.40"), gt=0, le=1)
    security_type_budgets: dict[str, Decimal] = Field(default_factory=dict)
    enforcement_mode: ConstraintEnforcementMode = ConstraintEnforcementMode.PROPORTIONAL_SCALE
    extra_symbol_caps: dict[str, Decimal] = Field(default_factory=dict)
    risk_budget: PortfolioRiskBudgetRequest | None = Field(default=None)

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
    cash: Decimal = Field(ge=0)
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


class PortfolioPlanRead(BaseModel):
    plan_id: int
    name: str
    as_of_date: date
    status: Literal["draft"] = "draft"
    execution_allowed: Literal[False] = False
    input_source: Literal["operator_provided"] = "operator_provided"
    total_equity: Decimal
    cash_before: Decimal
    target_cash: Decimal
    cash_after: Decimal
    constraints: PortfolioConstraintsRequest
    code_version: str
    created_at: datetime
    targets: list[PortfolioTargetRead]
    risk_budget: PortfolioRiskBudgetRequest | None = None


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
