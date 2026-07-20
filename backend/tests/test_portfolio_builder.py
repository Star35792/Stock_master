from decimal import Decimal

import pytest

from app.portfolio import PortfolioAsset, PortfolioBuilder, PortfolioConstraints


def test_portfolio_builder_applies_budgets_and_preserves_cash() -> None:
    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset("A", "stock", "Tech", Decimal("10"), 0, Decimal("0.50")),
            PortfolioAsset("B", "stock", "Tech", Decimal("20"), 0, Decimal("0.30")),
            PortfolioAsset("C", "etf", "ETF", Decimal("5"), 0, Decimal("0.30")),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0.10"),
            max_symbol_weight=Decimal("0.40"),
            max_industry_weight=Decimal("0.50"),
            security_type_budgets={
                "stock": Decimal("0.60"),
                "etf": Decimal("0.20"),
            },
        ),
    )

    targets = {target.symbol: target for target in plan.targets}
    assert (
        targets["A"].constrained_weight + targets["B"].constrained_weight
        == Decimal("0.50")
    )
    assert targets["C"].constrained_weight == Decimal("0.20")
    assert "max_symbol_weight" in targets["A"].reasons
    assert "industry_budget" in targets["A"].reasons
    assert "security_type_budget" in targets["C"].reasons
    assert plan.cash_after >= plan.target_cash
    assert sum((target.final_weight for target in plan.targets), Decimal()) <= Decimal("0.90")


def test_portfolio_builder_generates_exit_and_buy_quantities() -> None:
    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset("A", "stock", "Tech", Decimal("10"), 1000, Decimal("0")),
            PortfolioAsset("B", "stock", "Finance", Decimal("20"), 0, Decimal("0.50")),
        ],
        cash=Decimal("10000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0.10"),
            max_symbol_weight=Decimal("0.60"),
            max_industry_weight=Decimal("0.60"),
        ),
    )

    targets = {target.symbol: target for target in plan.targets}
    assert targets["A"].order_quantity == -1000
    assert targets["B"].order_quantity > 0
    assert targets["B"].order_quantity % 100 == 0


def test_portfolio_builder_rejects_negative_weights() -> None:
    with pytest.raises(ValueError, match="requested_weight"):
        PortfolioBuilder().build(
            assets=[
                PortfolioAsset(
                    "A",
                    "stock",
                    "Tech",
                    Decimal("10"),
                    0,
                    Decimal("-0.1"),
                )
            ],
            cash=Decimal("10000"),
            constraints=PortfolioConstraints(),
        )
