from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PortfolioAsset:
    symbol: str
    security_type: str
    industry: str
    price: Decimal
    current_quantity: int
    requested_weight: Decimal
    lot_size: int = 100


@dataclass(frozen=True)
class PortfolioConstraints:
    cash_reserve_rate: Decimal = Decimal("0.05")
    max_symbol_weight: Decimal = Decimal("0.20")
    max_industry_weight: Decimal = Decimal("0.40")
    security_type_budgets: dict[str, Decimal] | None = None


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
        weights = {
            asset.symbol: min(asset.requested_weight, constraints.max_symbol_weight)
            for asset in assets
        }
        reasons: dict[str, list[str]] = {asset.symbol: [] for asset in assets}
        for asset in assets:
            if asset.requested_weight > constraints.max_symbol_weight:
                reasons[asset.symbol].append("max_symbol_weight")

        self._scale_groups(
            assets=assets,
            weights=weights,
            reasons=reasons,
            group_getter=lambda asset: asset.security_type,
            limits=constraints.security_type_budgets or {},
            reason="security_type_budget",
        )
        industry_limits = {
            asset.industry: constraints.max_industry_weight for asset in assets
        }
        self._scale_groups(
            assets=assets,
            weights=weights,
            reasons=reasons,
            group_getter=lambda asset: asset.industry,
            limits=industry_limits,
            reason="industry_budget",
        )
        investable_weight = Decimal(1) - constraints.cash_reserve_rate
        total_weight = sum(weights.values(), Decimal())
        if total_weight > investable_weight:
            scale = investable_weight / total_weight
            for symbol in weights:
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

    @staticmethod
    def _scale_groups(
        *,
        assets: list[PortfolioAsset],
        weights: dict[str, Decimal],
        reasons: dict[str, list[str]],
        group_getter: Callable[[PortfolioAsset], str],
        limits: dict[str, Decimal],
        reason: str,
    ) -> None:
        groups: dict[str, list[PortfolioAsset]] = {}
        for asset in assets:
            group = group_getter(asset)
            groups.setdefault(group, []).append(asset)
        for group, group_assets in groups.items():
            limit = limits.get(group)
            group_weight = sum((weights[asset.symbol] for asset in group_assets), Decimal())
            if limit is None or group_weight <= limit:
                continue
            scale = limit / group_weight
            for asset in group_assets:
                weights[asset.symbol] *= scale
                reasons[asset.symbol].append(reason)

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
