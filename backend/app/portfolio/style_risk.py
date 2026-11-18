from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from math import sqrt

MIN_RISK_RETURN_OBSERVATIONS = 60


@dataclass(frozen=True)
class StyleExposure:
    style_name: str
    portfolio_exposure: Decimal
    benchmark_exposure: Decimal | None
    active_exposure: Decimal | None


@dataclass(frozen=True)
class PortfolioStyleRisk:
    style_exposures: tuple[StyleExposure, ...]
    tracking_error: Decimal | None
    portfolio_volatility: Decimal | None
    missing_styles: tuple[str, ...]
    missing_benchmark: bool
    factor_versions: dict[str, str] = field(default_factory=dict)
    factor_as_of_date: date | None = None
    excluded_styles: tuple[str, ...] = ()
    sample_start_date: date | None = None
    sample_end_date: date | None = None
    return_observation_count: int = 0
    factor_evidence: dict[str, dict[str, int | str]] = field(default_factory=dict)


def _to_float(value: Decimal) -> float:
    return float(value)


def _annualize(daily_std: Decimal, *, trading_days: int = 252) -> Decimal:
    return daily_std * Decimal(sqrt(trading_days))


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal()
    return sum(values, Decimal()) / Decimal(len(values))


def _std(values: list[Decimal]) -> Decimal:
    if len(values) < 2:
        return Decimal()
    mean = _mean(values)
    variance = sum(((v - mean) ** 2 for v in values), Decimal()) / Decimal(len(values) - 1)
    return Decimal(variance).sqrt()


def compute_returns(prices: list[Decimal]) -> list[Decimal]:
    if len(prices) < 2:
        return []
    if any(price <= 0 or not price.is_finite() for price in prices):
        return []
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


def compute_tracking_error(
    portfolio_returns: list[Decimal],
    benchmark_returns: list[Decimal],
) -> Decimal | None:
    if len(portfolio_returns) < 2 or len(portfolio_returns) != len(benchmark_returns):
        return None
    diffs = [portfolio - benchmark for portfolio, benchmark in zip(
        portfolio_returns, benchmark_returns, strict=True
    )]
    return _annualize(_std(diffs))


def compute_portfolio_volatility(
    portfolio_returns: list[Decimal],
) -> Decimal | None:
    if len(portfolio_returns) < 2:
        return None
    return _annualize(_std(portfolio_returns))


def compute_style_exposures(
    *,
    weights: dict[str, Decimal],
    style_factors: dict[str, dict[str, Decimal]],
    benchmark_weights: dict[str, Decimal] | None = None,
) -> tuple[list[StyleExposure], list[str]]:
    symbols = sorted(weights.keys())
    missing_styles: list[str] = []
    exposures: list[StyleExposure] = []
    for style_name in sorted(style_factors.keys()):
        factor_values = style_factors[style_name]
        available_symbols = [s for s in symbols if s in factor_values]
        if not available_symbols:
            missing_styles.append(style_name)
            exposures.append(
                StyleExposure(
                    style_name=style_name,
                    portfolio_exposure=Decimal(),
                    benchmark_exposure=None,
                    active_exposure=None,
                )
            )
            continue
        portfolio_exposure = sum(weights[s] * factor_values[s] for s in available_symbols)
        benchmark_exposure: Decimal | None = None
        active_exposure: Decimal | None = None
        if benchmark_weights is not None:
            bm_symbols = sorted(benchmark_weights.keys())
            bm_available = [s for s in bm_symbols if s in factor_values]
            if bm_available:
                total_bm_weight = sum(benchmark_weights[s] for s in bm_available)
                if total_bm_weight > 0:
                    normalized_bm = {
                        s: benchmark_weights[s] / total_bm_weight for s in bm_available
                    }
                    benchmark_exposure = sum(
                        normalized_bm[s] * factor_values[s] for s in bm_available
                    )
                    active_exposure = portfolio_exposure - benchmark_exposure
        exposures.append(
            StyleExposure(
                style_name=style_name,
                portfolio_exposure=portfolio_exposure,
                benchmark_exposure=benchmark_exposure,
                active_exposure=active_exposure,
            )
        )
    return exposures, missing_styles


def scale_style_exposures(
    *,
    weights: dict[str, Decimal],
    style_factors: dict[str, dict[str, Decimal]],
    max_active_exposure: Decimal,
    benchmark_weights: dict[str, Decimal] | None = None,
) -> dict[str, Decimal]:
    exposures, _ = compute_style_exposures(
        weights=weights,
        style_factors=style_factors,
        benchmark_weights=benchmark_weights,
    )
    max_abs = max(
        (
            abs(exp.portfolio_exposure if benchmark_weights is None else exp.active_exposure)
            for exp in exposures
            if benchmark_weights is None or exp.active_exposure is not None
        ),
        default=Decimal(),
    )
    if max_abs <= 0 or max_abs <= max_active_exposure:
        return dict(weights)
    scale = max_active_exposure / max_abs
    return {symbol: weight * scale for symbol, weight in weights.items()}


def build_portfolio_style_risk(
    *,
    weights: dict[str, Decimal],
    style_factors: dict[str, dict[str, Decimal]],
    portfolio_prices: dict[str, dict[date, Decimal]],
    benchmark_prices: dict[date, Decimal] | None = None,
    benchmark_weights: dict[str, Decimal] | None = None,
    factor_versions: dict[str, str] | None = None,
    factor_as_of_date: date | None = None,
    excluded_styles: tuple[str, ...] = (),
    factor_evidence: dict[str, dict[str, int | str]] | None = None,
) -> PortfolioStyleRisk:
    exposures, missing_styles = compute_style_exposures(
        weights=weights,
        style_factors=style_factors,
        benchmark_weights=benchmark_weights,
    )

    portfolio_returns: list[Decimal] = []
    symbols = sorted(symbol for symbol, weight in weights.items() if weight > 0)
    portfolio_dates: list[date] = []
    if symbols and all(portfolio_prices.get(symbol) for symbol in symbols):
        date_sets = [set(portfolio_prices[symbol]) for symbol in symbols]
        if all(dates == date_sets[0] for dates in date_sets[1:]):
            portfolio_dates = sorted(date_sets[0])
            price_series = [
                [portfolio_prices[symbol][day] for day in portfolio_dates]
                for symbol in symbols
            ]
            returns_by_symbol = [compute_returns(prices) for prices in price_series]
            if len(portfolio_dates) > MIN_RISK_RETURN_OBSERVATIONS and all(
                len(returns) == len(portfolio_dates) - 1 for returns in returns_by_symbol
            ):
                portfolio_returns = [
                    sum(
                        (
                            weights[symbol] * returns_by_symbol[j][i]
                            for j, symbol in enumerate(symbols)
                        ),
                        Decimal(),
                    )
                    for i in range(len(portfolio_dates) - 1)
                ]

    benchmark_returns: list[Decimal] = []
    if benchmark_prices is not None and set(benchmark_prices) == set(portfolio_dates):
        benchmark_returns = compute_returns([benchmark_prices[day] for day in portfolio_dates])

    tracking_error = None
    if benchmark_returns and portfolio_returns:
        tracking_error = compute_tracking_error(portfolio_returns, benchmark_returns)

    portfolio_volatility = compute_portfolio_volatility(portfolio_returns)

    return PortfolioStyleRisk(
        style_exposures=tuple(exposures),
        tracking_error=tracking_error,
        portfolio_volatility=portfolio_volatility,
        missing_styles=tuple(missing_styles),
        missing_benchmark=benchmark_prices is None or not benchmark_returns,
        factor_versions=factor_versions or {},
        factor_as_of_date=factor_as_of_date,
        excluded_styles=excluded_styles,
        sample_start_date=portfolio_dates[0] if portfolio_returns else None,
        sample_end_date=portfolio_dates[-1] if portfolio_returns else None,
        return_observation_count=len(portfolio_returns),
        factor_evidence=factor_evidence or {},
    )
