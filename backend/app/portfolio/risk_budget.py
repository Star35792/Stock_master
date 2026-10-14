from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PortfolioRiskBudget:
    """Risk budget constraints applied at portfolio construction time.

    The budget is declarative and may not be fully enforceable without
    additional data (style factors, benchmark returns, portfolio volatility
    estimates). Callers must inspect ``PortfolioPlan`` reasons to see which
    budget components were actually applied.
    """

    max_industry_weight: Decimal = Decimal("0.40")
    max_style_exposure: Decimal | None = None
    max_tracking_error: Decimal | None = None
    max_portfolio_volatility: Decimal | None = None
    benchmark: str | None = None
