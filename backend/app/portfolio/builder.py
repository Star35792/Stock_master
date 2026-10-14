from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.portfolio.enums import ConstraintEnforcementMode


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
    enforcement_mode: ConstraintEnforcementMode = (
        ConstraintEnforcementMode.PROPORTIONAL_SCALE
    )
    extra_symbol_caps: dict[str, Decimal] = field(default_factory=dict)


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


class PortfolioBuilder:
    def build(
        self,
        *,
        assets: list[PortfolioAsset],
        cash: Decimal,
        constraints: PortfolioConstraints,
    ) -> PortfolioPlan:
        self._validate(assets, cash, constraints)
        total_equity = cash + sum(
            (asset.price * Decimal(asset.current_quantity) for asset in assets),
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
            asset.symbol: min(asset.requested_weight, symbol_caps[asset.symbol])
            for asset in assets
        }
        reasons: dict[str, list[str]] = {asset.symbol: [] for asset in assets}
        for asset in assets:
            if asset.requested_weight > symbol_caps[asset.symbol]:
                reasons[asset.symbol].append("max_symbol_weight")

        self._enforce_group(
            assets=assets,
            weights=weights,
            reasons=reasons,
            group_getter=lambda asset: asset.security_type,
            limits=constraints.security_type_budgets or {},
            reason="security_type_budget",
            mode=constraints.enforcement_mode,
        )
        industry_limits = {
            asset.industry: constraints.max_industry_weight for asset in assets
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
            target_value = Decimal(target_quantity) * asset.price
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
        target_cash = total_equity * constraints.cash_reserve_rate
        cash_after = total_equity - invested_value
        if cash_after < target_cash:
            raise ValueError("portfolio construction produced insufficient cash reserve")
        return PortfolioPlan(
            total_equity=total_equity,
            cash_before=cash,
            target_cash=target_cash,
            cash_after=cash_after,
            targets=tuple(targets),
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
                raise ValueError(
                    f"extra symbol cap references unknown symbol {symbol!r}"
                )
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
            missing = sorted(
                asset.symbol for asset in assets if asset.score is None
            )
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
