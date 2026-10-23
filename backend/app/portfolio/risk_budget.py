from dataclasses import dataclass
from decimal import Decimal

from app.portfolio.defaults import (
    DEFAULT_MAX_INDUSTRY_WEIGHT,
    DEFAULT_MAX_PORTFOLIO_VOLATILITY,
    DEFAULT_MAX_STYLE_EXPOSURE,
    DEFAULT_MAX_TRACKING_ERROR,
)


@dataclass(frozen=True)
class PortfolioRiskBudget:
    """Risk budget constraints applied at portfolio construction time.

    The budget is declarative and may not be fully enforceable without
    additional data (style factors, benchmark returns, portfolio volatility
    estimates). Callers must inspect ``PortfolioPlan`` reasons to see which
    budget components were actually applied.
    """

    max_industry_weight: Decimal = DEFAULT_MAX_INDUSTRY_WEIGHT
    max_style_exposure: Decimal | None = DEFAULT_MAX_STYLE_EXPOSURE
    max_tracking_error: Decimal | None = DEFAULT_MAX_TRACKING_ERROR
    max_portfolio_volatility: Decimal | None = DEFAULT_MAX_PORTFOLIO_VOLATILITY
    benchmark: str | None = None
