"""Default values for portfolio construction and risk-budget constraints.

Why this module exists
----------------------

The same numbers used to be written out in three places: the
``PortfolioRiskBudget`` dataclass, the ``PortfolioRiskBudgetRequest`` schema and
the ``PortfolioConstraintsRequest`` schema.  Any change to one of them silently
desynchronised the others, so "what is the industry cap?" had no single answer.
This module is that single answer.

Behaviour note
--------------

The numeric values here are the historical defaults and changing them changes
portfolio results, so they are *not* retuned as part of moving them here.  The
style / tracking-error / volatility budgets stay ``None`` (opt-in) because
turning them on without factor IC evidence or a benchmark series would be
unverifiable tuning; they are exposed as named constants so a future change has
one place to make it and one place to review.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

__all__ = [
    "DEFAULT_BENCHMARK",
    "DEFAULT_CASH_RESERVE_RATE",
    "DEFAULT_MAX_INDUSTRY_WEIGHT",
    "DEFAULT_MAX_PORTFOLIO_VOLATILITY",
    "DEFAULT_MAX_STYLE_EXPOSURE",
    "DEFAULT_MAX_SYMBOL_WEIGHT",
    "DEFAULT_MAX_TRACKING_ERROR",
    "PortfolioPolicyDefaults",
]

#: Maximum share of a portfolio in one industry.
DEFAULT_MAX_INDUSTRY_WEIGHT = Decimal("0.40")

#: Maximum share of a portfolio in one security.
DEFAULT_MAX_SYMBOL_WEIGHT = Decimal("0.20")

#: Share of portfolio value kept in cash.
DEFAULT_CASH_RESERVE_RATE = Decimal("0.05")

#: Opt-in budgets.  ``None`` means "not enforced".
DEFAULT_MAX_STYLE_EXPOSURE: Decimal | None = None
DEFAULT_MAX_TRACKING_ERROR: Decimal | None = None
DEFAULT_MAX_PORTFOLIO_VOLATILITY: Decimal | None = None
DEFAULT_BENCHMARK: str | None = None


@dataclass(frozen=True)
class PortfolioPolicyDefaults:
    """Resolved default policy, overridable from application settings.

    Constructed from :class:`app.config.settings.Settings` so an operator can
    change the caps without a code change, while callers that pass an explicit
    value always win.
    """

    max_industry_weight: Decimal = DEFAULT_MAX_INDUSTRY_WEIGHT
    max_symbol_weight: Decimal = DEFAULT_MAX_SYMBOL_WEIGHT
    cash_reserve_rate: Decimal = DEFAULT_CASH_RESERVE_RATE
    max_style_exposure: Decimal | None = DEFAULT_MAX_STYLE_EXPOSURE
    max_tracking_error: Decimal | None = DEFAULT_MAX_TRACKING_ERROR
    max_portfolio_volatility: Decimal | None = DEFAULT_MAX_PORTFOLIO_VOLATILITY

    @classmethod
    def from_settings(cls, settings_obj: Any) -> PortfolioPolicyDefaults:
        """Build defaults from settings, tolerating a partially configured object."""

        def pick(name: str, fallback: Any) -> Any:
            value = getattr(settings_obj, name, None)
            return fallback if value is None else value

        return cls(
            max_industry_weight=pick(
                "portfolio_max_industry_weight", DEFAULT_MAX_INDUSTRY_WEIGHT
            ),
            max_symbol_weight=pick(
                "portfolio_max_symbol_weight", DEFAULT_MAX_SYMBOL_WEIGHT
            ),
            cash_reserve_rate=pick(
                "portfolio_cash_reserve_rate", DEFAULT_CASH_RESERVE_RATE
            ),
            max_style_exposure=pick(
                "portfolio_max_style_exposure", DEFAULT_MAX_STYLE_EXPOSURE
            ),
            max_tracking_error=pick(
                "portfolio_max_tracking_error", DEFAULT_MAX_TRACKING_ERROR
            ),
            max_portfolio_volatility=pick(
                "portfolio_max_portfolio_volatility",
                DEFAULT_MAX_PORTFOLIO_VOLATILITY,
            ),
        )
