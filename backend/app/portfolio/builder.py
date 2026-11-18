from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any

from app.portfolio.enums import ConstraintEnforcementMode
from app.portfolio.risk_budget import PortfolioRiskBudget
from app.portfolio.style_risk import (
    PortfolioStyleRisk,
    build_portfolio_style_risk,
    scale_style_exposures,
)

MONEY_QUANTUM = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:
    """Keep valuation and cash arithmetic at the plan ledger's four-place scale."""
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


class _SymbolCapReason(StrEnum):
    max_symbol_weight = "max_symbol_weight"


@dataclass(frozen=True)
class PortfolioAsset:
    symbol: str
    security_type: str
    industry: str
    price: Decimal
    current_quantity: int
    requested_weight: Decimal
    lot_size: int = 100
    score: Decimal | None = None


@dataclass(frozen=True)
class PortfolioConstraints:
    cash_reserve_rate: Decimal = Decimal("0.05")
    max_symbol_weight: Decimal = Decimal("0.20")
    max_industry_weight: Decimal = Decimal("0.40")
    security_type_budgets: dict[str, Decimal] | None = None
    enforcement_mode: ConstraintEnforcementMode = ConstraintEnforcementMode.PROPORTIONAL_SCALE
    extra_symbol_caps: dict[str, Decimal] = field(default_factory=dict)
    risk_budget: PortfolioRiskBudget | None = None


@dataclass(frozen=True)
class PortfolioTarget:
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
    reasons: tuple[str, ...]
    score: Decimal | None = None


@dataclass(frozen=True)
class PortfolioPlan:
    total_equity: Decimal
    cash_before: Decimal
    target_cash: Decimal
    cash_after: Decimal
    targets: tuple[PortfolioTarget, ...]
    style_risk: PortfolioStyleRisk | None = None


@dataclass(frozen=True)
class _RiskInputSnapshot:
    style_factors: dict[str, dict[str, Decimal]]
    portfolio_prices: dict[str, dict[date, Decimal]]
    benchmark_prices: dict[date, Decimal] | None
    factor_versions: dict[str, str]
    factor_as_of_date: date | None
    excluded_styles: tuple[str, ...]
    factor_evidence: dict[str, dict[str, int | str]]


class PortfolioBuilder:
    def __init__(
        self,
        style_data_service: Any | None = None,
    ) -> None:
        self.style_data_service = style_data_service

    def build(
        self,
        *,
        assets: list[PortfolioAsset],
        cash: Decimal,
        constraints: PortfolioConstraints,
        db: Any | None = None,
        as_of_date: Any | None = None,
        style_factor_map: dict[str, tuple[str, str]] | None = None,
        benchmark_symbol: str | None = None,
    ) -> PortfolioPlan:
        self._validate(assets, cash, constraints)
        total_equity = _money(cash) + sum(
            (_money(asset.price * Decimal(asset.current_quantity)) for asset in assets),
            Decimal(),
        )
        symbol_caps = {
            asset.symbol: min(
                constraints.max_symbol_weight,
                constraints.extra_symbol_caps.get(asset.symbol, Decimal(1)),
            )
            for asset in assets
        }
        weights = {
            asset.symbol: min(asset.requested_weight, symbol_caps[asset.symbol]) for asset in assets
        }
        reasons: dict[str, list[str]] = {asset.symbol: [] for asset in assets}
        for asset in assets:
            if asset.requested_weight > symbol_caps[asset.symbol]:
                reasons[asset.symbol].append("max_symbol_weight")

        style_risk, risk_inputs = self._apply_style_and_tracking_risk(
            assets=assets,
            weights=weights,
            reasons=reasons,
            constraints=constraints,
            db=db,
            as_of_date=as_of_date,
            style_factor_map=style_factor_map,
            benchmark_symbol=benchmark_symbol,
        )

        self._enforce_group(
            assets=assets,
            weights=weights,
            reasons=reasons,
            group_getter=lambda asset: asset.security_type,
            limits=constraints.security_type_budgets or {},
            reason="security_type_budget",
            mode=constraints.enforcement_mode,
        )
        risk_budget_industry_limit = self._effective_industry_limit(constraints)
        industry_limits = {
            asset.industry: min(constraints.max_industry_weight, risk_budget_industry_limit)
            for asset in assets
        }
        self._enforce_group(
            assets=assets,
            weights=weights,
            reasons=reasons,
            group_getter=lambda asset: asset.industry,
            limits=industry_limits,
            reason="industry_budget",
            mode=constraints.enforcement_mode,
        )

        # Single-symbol caps are already enforced by the min() at construction;
        # _scale_group and _trim_group are both monotonically decreasing, so no
        # re-capping pass is needed or possible here.
        investable_weight = Decimal(1) - constraints.cash_reserve_rate
        total_weight = sum(weights.values(), Decimal())
        if total_weight > investable_weight:
            scale = investable_weight / total_weight
            for symbol in weights:
                if weights[symbol] > 0:
                    weights[symbol] *= scale
                    reasons[symbol].append("cash_reserve")

        targets = []
        invested_value = Decimal()
        for asset in sorted(assets, key=lambda item: item.symbol):
            constrained_weight = weights[asset.symbol]
            raw_quantity = total_equity * constrained_weight / asset.price
            target_quantity = int(raw_quantity) // asset.lot_size * asset.lot_size
            target_value = _money(Decimal(target_quantity) * asset.price)
            final_weight = target_value / total_equity if total_equity else Decimal()
            if Decimal(target_quantity) != raw_quantity:
                reasons[asset.symbol].append("lot_rounding")
            if constrained_weight > 0 and target_quantity == 0:
                reasons[asset.symbol].append("budget_unallocated")
            invested_value += target_value
            targets.append(
                PortfolioTarget(
                    symbol=asset.symbol,
                    security_type=asset.security_type,
                    industry=asset.industry,
                    requested_weight=asset.requested_weight,
                    constrained_weight=constrained_weight,
                    final_weight=final_weight,
                    current_quantity=asset.current_quantity,
                    target_quantity=target_quantity,
                    order_quantity=target_quantity - asset.current_quantity,
                    price=asset.price,
                    reasons=tuple(reasons[asset.symbol]),
                    score=asset.score,
                )
            )
        target_cash = (total_equity * constraints.cash_reserve_rate).quantize(
            MONEY_QUANTUM, rounding=ROUND_CEILING
        )
        cash_after = total_equity - invested_value
        if cash_after < target_cash:
            raise ValueError("portfolio construction produced insufficient cash reserve")
        if risk_inputs is not None:
            style_risk = build_portfolio_style_risk(
                weights={target.symbol: target.final_weight for target in targets},
                style_factors=risk_inputs.style_factors,
                portfolio_prices=risk_inputs.portfolio_prices,
                benchmark_prices=risk_inputs.benchmark_prices,
                factor_versions=risk_inputs.factor_versions,
                factor_as_of_date=risk_inputs.factor_as_of_date,
                excluded_styles=risk_inputs.excluded_styles,
                factor_evidence=risk_inputs.factor_evidence,
            )
            budget = constraints.risk_budget
            assert budget is not None
            if budget.max_style_exposure is not None:
                if not style_risk.style_exposures or style_risk.missing_styles:
                    raise ValueError("style exposure budget requires complete style evidence")
                if any(
                    abs(exposure.portfolio_exposure) > budget.max_style_exposure
                    for exposure in style_risk.style_exposures
                ):
                    raise ValueError("final portfolio exceeds style exposure budget")
            if budget.max_tracking_error is not None and (
                style_risk.tracking_error is None
                or style_risk.tracking_error > budget.max_tracking_error
            ):
                raise ValueError("final portfolio exceeds tracking error budget")
            if budget.max_portfolio_volatility is not None and (
                style_risk.portfolio_volatility is None
                or style_risk.portfolio_volatility > budget.max_portfolio_volatility
            ):
                raise ValueError("final portfolio exceeds volatility budget")
        return PortfolioPlan(
            total_equity=total_equity,
            cash_before=_money(cash),
            target_cash=target_cash,
            cash_after=cash_after,
            targets=tuple(targets),
            style_risk=style_risk,
        )

    @staticmethod
    def _effective_industry_limit(constraints: PortfolioConstraints) -> Decimal:
        """Return the effective industry weight limit, combining legacy and budget limits."""
        limit = constraints.max_industry_weight
        if constraints.risk_budget is not None:
            limit = min(limit, constraints.risk_budget.max_industry_weight)
        return limit

    def _apply_style_and_tracking_risk(
        self,
        *,
        assets: list[PortfolioAsset],
        weights: dict[str, Decimal],
        reasons: dict[str, list[str]],
        constraints: PortfolioConstraints,
        db: Any | None,
        as_of_date: Any | None,
        style_factor_map: dict[str, tuple[str, str]] | None,
        benchmark_symbol: str | None,
    ) -> tuple[PortfolioStyleRisk | None, _RiskInputSnapshot | None]:
        if constraints.risk_budget is None:
            return None, None
        budget = constraints.risk_budget
        need_style = budget.max_style_exposure is not None
        need_te = budget.max_tracking_error is not None
        need_vol = budget.max_portfolio_volatility is not None
        if not need_style and not need_te and not need_vol:
            return None, None
        if db is None or as_of_date is None:
            raise ValueError("style/tracking/volatility risk budgets require db and as_of_date")
        if self.style_data_service is None:
            from app.services.portfolio_style_data import PortfolioStyleDataService

            self.style_data_service = PortfolioStyleDataService()
        symbols = sorted(symbol for symbol, weight in weights.items() if weight > 0)
        style_factors: dict[str, dict[str, Decimal]] = {}
        factor_versions: dict[str, str] = {}
        excluded_styles: tuple[str, ...] = ()
        factor_evidence: dict[str, dict[str, int | str]] = {}
        if need_style:
            from app.services.portfolio_style_data import DEFAULT_STYLE_FACTOR_MAP

            selected_map = style_factor_map or DEFAULT_STYLE_FACTOR_MAP
            loaded = self.style_data_service.load_style_exposures(
                db,
                as_of_date=as_of_date,
                symbols=symbols,
                style_factor_map=selected_map,
                include_valuation_styles=False,
                strict_budget=True,
                factor_evidence=factor_evidence,
            )
            missing_styles = set(selected_map) - set(loaded)
            if missing_styles:
                raise ValueError(
                    "style exposure budget requires all configured style factors: "
                    + ", ".join(sorted(missing_styles))
                )
            style_factors = {style: loaded[style] for style in selected_map}
            missing_values = {
                style: sorted(
                    symbol for symbol, weight in weights.items()
                    if weight > 0 and (
                        symbol not in values or not values[symbol].is_finite()
                    )
                )
                for style, values in style_factors.items()
            }
            missing_values = {style: items for style, items in missing_values.items() if items}
            if missing_values:
                raise ValueError(f"style factor evidence missing on {as_of_date}: {missing_values}")
            factor_versions = {
                style: version for style, (_factor, version) in selected_map.items()
            }
            excluded_styles = tuple(sorted({"size", "value"} - set(selected_map)))
        if benchmark_symbol is None:
            benchmark_symbol = budget.benchmark
        if need_te and benchmark_symbol is None:
            raise ValueError("tracking error risk budget requires a benchmark symbol")

        portfolio_prices: dict[str, dict[date, Decimal]] = {}
        benchmark_prices: dict[date, Decimal] | None = None
        if need_te or need_vol:
            portfolio_prices, benchmark_prices = self.style_data_service.load_risk_prices(
                db,
                symbols=symbols,
                benchmark_symbol=benchmark_symbol if need_te else None,
                end_date=as_of_date,
            )
        if need_te and benchmark_prices is None:
            raise ValueError(f"benchmark {benchmark_symbol} price history unavailable")

        if need_style:
            max_style = budget.max_style_exposure
            assert max_style is not None
            scaled = scale_style_exposures(
                weights=weights,
                style_factors=style_factors,
                max_active_exposure=max_style,
            )
            if scaled != weights:
                for symbol in weights:
                    weights[symbol] = scaled[symbol]
                    reasons[symbol].append("style_budget")

        if need_te or need_vol:
            missing_latest = [
                symbol for symbol, weight in weights.items()
                if weight > 0 and as_of_date not in portfolio_prices.get(symbol, {})
            ]
            if missing_latest:
                raise ValueError(
                    "curated QFQ prices missing on portfolio as_of_date: "
                    + ", ".join(sorted(missing_latest))
                )
            if need_te and (benchmark_prices is None or as_of_date not in benchmark_prices):
                raise ValueError("benchmark QFQ price missing on portfolio as_of_date")

        def risk_report() -> PortfolioStyleRisk:
            return build_portfolio_style_risk(
                weights=weights,
                style_factors=style_factors,
                portfolio_prices=portfolio_prices,
                benchmark_prices=benchmark_prices,
                factor_versions=factor_versions,
                factor_as_of_date=as_of_date if need_style else None,
                excluded_styles=excluded_styles,
                factor_evidence=factor_evidence,
            )

        style_risk = risk_report()

        if need_te:
            max_te = budget.max_tracking_error
            assert max_te is not None
            if style_risk.tracking_error is None:
                raise ValueError("tracking error required but could not be computed")
            if style_risk.tracking_error > max_te:
                scale = max_te / style_risk.tracking_error
                for symbol in weights:
                    if weights[symbol] > 0:
                        weights[symbol] *= scale
                        reasons[symbol].append("tracking_error_budget")
                style_risk = risk_report()

        if need_vol:
            max_vol = budget.max_portfolio_volatility
            assert max_vol is not None
            if style_risk.portfolio_volatility is None:
                raise ValueError("portfolio volatility required but could not be computed")
            if style_risk.portfolio_volatility > max_vol:
                scale = max_vol / style_risk.portfolio_volatility
                # Slightly tighten the scale to absorb tiny Decimal/rounding drift
                # so the recomputed volatility is guaranteed to sit inside the budget.
                scale = (scale * Decimal("0.9999999999")).quantize(Decimal("0.0000000001"))
                for symbol in weights:
                    if weights[symbol] > 0:
                        weights[symbol] *= scale
                        reasons[symbol].append("volatility_budget")
                style_risk = risk_report()

        return style_risk, _RiskInputSnapshot(
            style_factors=style_factors,
            portfolio_prices=portfolio_prices,
            benchmark_prices=benchmark_prices,
            factor_versions=factor_versions,
            factor_as_of_date=as_of_date if need_style else None,
            excluded_styles=excluded_styles,
            factor_evidence=factor_evidence,
        )

    def _enforce_group(
        self,
        *,
        assets: list[PortfolioAsset],
        weights: dict[str, Decimal],
        reasons: dict[str, list[str]],
        group_getter: Callable[[PortfolioAsset], str],
        limits: dict[str, Decimal],
        reason: str,
        mode: ConstraintEnforcementMode,
    ) -> None:
        groups: dict[str, list[PortfolioAsset]] = {}
        for asset in assets:
            group = group_getter(asset)
            groups.setdefault(group, []).append(asset)
        for group, group_assets in groups.items():
            limit = limits.get(group)
            if limit is None:
                continue
            group_weight = sum((weights[asset.symbol] for asset in group_assets), Decimal())
            if group_weight <= limit:
                continue
            if mode is ConstraintEnforcementMode.PROPORTIONAL_SCALE:
                self._scale_group(group_assets, weights, reasons, limit, reason)
                continue
            if any(asset.score is None for asset in group_assets):
                raise ValueError(
                    "score_ranked_trim requires a score on every asset in group "
                    f"{group!r}; missing scores cannot be ranked safely"
                )
            self._trim_group(group_assets, weights, reasons, limit, reason)

    @staticmethod
    def _scale_group(
        group_assets: list[PortfolioAsset],
        weights: dict[str, Decimal],
        reasons: dict[str, list[str]],
        limit: Decimal,
        reason: str,
    ) -> None:
        group_weight = sum((weights[asset.symbol] for asset in group_assets), Decimal())
        if group_weight <= 0:
            return
        scale = limit / group_weight
        for asset in group_assets:
            weights[asset.symbol] *= scale
            reasons[asset.symbol].append(reason)

    @staticmethod
    def _trim_group(
        group_assets: list[PortfolioAsset],
        weights: dict[str, Decimal],
        reasons: dict[str, list[str]],
        limit: Decimal,
        reason: str,
    ) -> None:
        """Trim a group to its limit by removing weight from the lowest scores first.

        Highest scores keep their full requested weight; the excess is taken from
        the weakest names, and any symbol reduced to zero leaves the portfolio.
        """

        ordered = sorted(
            group_assets,
            key=lambda asset: (_score_of(asset), asset.symbol),
        )
        excess = sum((weights[asset.symbol] for asset in group_assets), Decimal()) - limit
        if excess <= 0:
            return
        for asset in ordered:
            if excess <= 0:
                break
            current = weights[asset.symbol]
            if current <= 0:
                continue
            reduction = min(current, excess)
            weights[asset.symbol] = current - reduction
            excess -= reduction
            reasons[asset.symbol].append(reason)
            if weights[asset.symbol] == 0:
                reasons[asset.symbol].append("trimmed_to_zero")

    @staticmethod
    def _validate(
        assets: list[PortfolioAsset],
        cash: Decimal,
        constraints: PortfolioConstraints,
    ) -> None:
        if cash < 0:
            raise ValueError("cash must be non-negative")
        if not Decimal() <= constraints.cash_reserve_rate < Decimal(1):
            raise ValueError("cash_reserve_rate must be between 0 and 1")
        if constraints.max_symbol_weight <= 0 or constraints.max_symbol_weight > 1:
            raise ValueError("max_symbol_weight must be between 0 and 1")
        if constraints.max_industry_weight <= 0 or constraints.max_industry_weight > 1:
            raise ValueError("max_industry_weight must be between 0 and 1")
        for budget in (constraints.security_type_budgets or {}).values():
            if budget < 0 or budget > 1:
                raise ValueError("security type budgets must be between 0 and 1")
        for symbol, cap in constraints.extra_symbol_caps.items():
            if cap <= 0 or cap > 1:
                raise ValueError("extra symbol caps must be within (0, 1]")
            if symbol not in {asset.symbol for asset in assets}:
                raise ValueError(f"extra symbol cap references unknown symbol {symbol!r}")
        if len({asset.symbol for asset in assets}) != len(assets):
            raise ValueError("asset symbols must be unique")
        for asset in assets:
            if asset.price <= 0:
                raise ValueError("asset price must be positive")
            if asset.current_quantity < 0:
                raise ValueError("current_quantity must be non-negative")
            if asset.requested_weight < 0:
                raise ValueError("requested_weight must be non-negative")
            if asset.lot_size <= 0:
                raise ValueError("lot_size must be positive")
        if constraints.enforcement_mode is ConstraintEnforcementMode.SCORE_RANKED_TRIM:
            missing = sorted(asset.symbol for asset in assets if asset.score is None)
            if missing:
                raise ValueError(
                    "score_ranked_trim requires a score on every asset; missing: "
                    + ", ".join(missing)
                )
            for asset in assets:
                if not asset.score.is_finite():
                    raise ValueError(
                        f"score_ranked_trim requires finite scores; "
                        f"{asset.symbol!r} got {asset.score}"
                    )


def _score_of(asset: PortfolioAsset) -> Decimal:
    return asset.score if asset.score is not None else Decimal(0)
