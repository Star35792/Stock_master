from dataclasses import dataclass
from decimal import Decimal
from math import sqrt


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
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


def compute_tracking_error(
    portfolio_returns: list[Decimal],
    benchmark_returns: list[Decimal],
) -> Decimal | None:
    if len(portfolio_returns) < 2 or len(benchmark_returns) < 2:
        return None
    n = min(len(portfolio_returns), len(benchmark_returns))
    diffs = [portfolio_returns[-(i + 1)] - benchmark_returns[-(i + 1)] for i in range(n)]
    return _annualize(_std(list(reversed(diffs))))


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
            # If benchmark symbols are not present in the cross-sectional factor
            # snapshot, fall back to portfolio exposure as the active exposure so
            # that style budgets still constrain the absolute portfolio tilt.
            if active_exposure is None:
                active_exposure = portfolio_exposure
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
        (abs(exp.active_exposure) for exp in exposures if exp.active_exposure is not None),
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
    portfolio_prices: dict[str, list[Decimal]],
    benchmark_prices: list[Decimal] | None = None,
) -> PortfolioStyleRisk:
    exposures, missing_styles = compute_style_exposures(
        weights=weights,
        style_factors=style_factors,
    )

    portfolio_returns: list[Decimal] = []
    symbols = sorted(weights.keys())
    if symbols and portfolio_prices:
        aligned_prices: dict[str, list[Decimal]] = {s: portfolio_prices.get(s, []) for s in symbols}
        min_len = min(len(p) for p in aligned_prices.values())
        if min_len >= 2:
            for i in range(1, min_len):
                day_return = Decimal()
                for s in symbols:
                    prev = aligned_prices[s][i - 1]
                    curr = aligned_prices[s][i]
                    if prev > 0:
                        day_return += weights[s] * (curr - prev) / prev
                portfolio_returns.append(day_return)

    benchmark_returns: list[Decimal] = []
    if benchmark_prices is not None and len(benchmark_prices) >= 2:
        benchmark_returns = compute_returns(benchmark_prices)

    tracking_error = None
    if benchmark_returns and portfolio_returns:
        tracking_error = compute_tracking_error(portfolio_returns, benchmark_returns)

    portfolio_volatility = compute_portfolio_volatility(portfolio_returns)

    return PortfolioStyleRisk(
        style_exposures=tuple(exposures),
        tracking_error=tracking_error,
        portfolio_volatility=portfolio_volatility,
        missing_styles=tuple(missing_styles),
        missing_benchmark=benchmark_prices is None,
    )
