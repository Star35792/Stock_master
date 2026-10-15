from decimal import Decimal

import pytest

from app.portfolio import (
    ConstraintEnforcementMode,
    PortfolioAsset,
    PortfolioBuilder,
    PortfolioConstraints,
    PortfolioRiskBudget,
)


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
    assert targets["A"].constrained_weight + targets["B"].constrained_weight == Decimal("0.50")
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


def test_score_ranked_trim_removes_the_weakest_names_instead_of_scaling() -> None:
    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset(
                "A",
                "stock",
                "Tech",
                Decimal("10"),
                0,
                Decimal("0.30"),
                score=Decimal("0.9"),
            ),
            PortfolioAsset(
                "B",
                "stock",
                "Tech",
                Decimal("10"),
                0,
                Decimal("0.30"),
                score=Decimal("0.5"),
            ),
            PortfolioAsset(
                "C",
                "stock",
                "Tech",
                Decimal("10"),
                0,
                Decimal("0.30"),
                score=Decimal("0.1"),
            ),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("1"),
            max_industry_weight=Decimal("0.40"),
            enforcement_mode=ConstraintEnforcementMode.SCORE_RANKED_TRIM,
        ),
    )

    targets = {target.symbol: target for target in plan.targets}
    assert targets["A"].constrained_weight == Decimal("0.30")
    assert targets["C"].constrained_weight == Decimal("0")
    assert targets["B"].constrained_weight == Decimal("0.10")
    assert "trimmed_to_zero" in targets["C"].reasons
    assert "industry_budget" in targets["C"].reasons


def test_proportional_scale_is_preserved_as_the_default_behavior() -> None:
    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset("A", "stock", "Tech", Decimal("10"), 0, Decimal("0.30")),
            PortfolioAsset("B", "stock", "Tech", Decimal("10"), 0, Decimal("0.30")),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("1"),
            max_industry_weight=Decimal("0.40"),
        ),
    )

    targets = {target.symbol: target for target in plan.targets}
    assert targets["A"].constrained_weight == Decimal("0.20")
    assert targets["B"].constrained_weight == Decimal("0.20")


def test_score_ranked_trim_requires_a_score_on_every_asset() -> None:
    with pytest.raises(ValueError, match="requires a score"):
        PortfolioBuilder().build(
            assets=[
                PortfolioAsset(
                    "A",
                    "stock",
                    "Tech",
                    Decimal("10"),
                    0,
                    Decimal("0.30"),
                    score=Decimal("0.9"),
                ),
                PortfolioAsset("B", "stock", "Tech", Decimal("10"), 0, Decimal("0.30")),
            ],
            cash=Decimal("100000"),
            constraints=PortfolioConstraints(
                enforcement_mode=ConstraintEnforcementMode.SCORE_RANKED_TRIM,
            ),
        )


def test_symbol_caps_hold_as_an_invariant_through_group_enforcement() -> None:
    """Caps are applied at construction; group enforcement only decreases weights."""

    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset(
                "A",
                "stock",
                "Tech",
                Decimal("10"),
                0,
                Decimal("0.45"),
                score=Decimal("0.9"),
            ),
            PortfolioAsset(
                "B",
                "stock",
                "Tech",
                Decimal("10"),
                0,
                Decimal("0.45"),
                score=Decimal("0.4"),
            ),
            PortfolioAsset(
                "C",
                "stock",
                "Finance",
                Decimal("10"),
                0,
                Decimal("0.45"),
                score=Decimal("0.6"),
            ),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("0.20"),
            max_industry_weight=Decimal("0.60"),
            security_type_budgets={"stock": Decimal("0.50")},
            enforcement_mode=ConstraintEnforcementMode.SCORE_RANKED_TRIM,
            extra_symbol_caps={"C": Decimal("0.10")},
        ),
    )

    caps = {"A": Decimal("0.20"), "B": Decimal("0.20"), "C": Decimal("0.10")}
    for target in plan.targets:
        assert target.constrained_weight <= caps[target.symbol], (
            f"{target.symbol} exceeded its cap: {target.constrained_weight}"
        )
    industry_total = sum(
        (t.constrained_weight for t in plan.targets if t.industry == "Tech"),
        Decimal(),
    )
    assert industry_total <= Decimal("0.60")
    assert "symbol_cap_reapplied" not in {reason for t in plan.targets for reason in t.reasons}


def test_extra_symbol_cap_tightens_a_single_name() -> None:
    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset("A", "stock", "Tech", Decimal("10"), 0, Decimal("0.40")),
            PortfolioAsset("B", "stock", "Finance", Decimal("10"), 0, Decimal("0.40")),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("0.40"),
            max_industry_weight=Decimal("1"),
            extra_symbol_caps={"A": Decimal("0.10")},
        ),
    )

    targets = {target.symbol: target for target in plan.targets}
    assert targets["A"].constrained_weight == Decimal("0.10")
    assert "max_symbol_weight" in targets["A"].reasons


def test_score_ranked_trim_records_unallocated_budget_when_lot_rounding_zeros() -> None:
    """A tiny constrained weight that rounds to zero quantity must be auditable."""
    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset(
                "A",
                "stock",
                "Tech",
                Decimal("100000"),
                0,
                Decimal("0.30"),
                score=Decimal("0.9"),
                lot_size=100,
            ),
            PortfolioAsset(
                "B",
                "stock",
                "Tech",
                Decimal("100000"),
                0,
                Decimal("0.00001"),
                score=Decimal("0.1"),
                lot_size=100,
            ),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("1"),
            max_industry_weight=Decimal("1"),
            enforcement_mode=ConstraintEnforcementMode.SCORE_RANKED_TRIM,
        ),
    )

    targets = {target.symbol: target for target in plan.targets}
    assert targets["B"].constrained_weight == Decimal("0.00001")
    assert targets["B"].target_quantity == 0
    assert "budget_unallocated" in targets["B"].reasons


def test_score_ranked_trim_rejects_non_finite_scores() -> None:
    with pytest.raises(ValueError, match="finite"):
        PortfolioBuilder().build(
            assets=[
                PortfolioAsset(
                    "A",
                    "stock",
                    "Tech",
                    Decimal("10"),
                    0,
                    Decimal("0.30"),
                    score=Decimal("NaN"),
                ),
            ],
            cash=Decimal("100000"),
            constraints=PortfolioConstraints(
                enforcement_mode=ConstraintEnforcementMode.SCORE_RANKED_TRIM,
            ),
        )


def test_risk_budget_tightens_industry_limit() -> None:
    """A risk budget with a lower industry cap than PortfolioConstraints wins."""

    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset("A", "stock", "Tech", Decimal("10"), 0, Decimal("0.30")),
            PortfolioAsset("B", "stock", "Tech", Decimal("10"), 0, Decimal("0.30")),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("1"),
            max_industry_weight=Decimal("0.50"),
            risk_budget=PortfolioRiskBudget(max_industry_weight=Decimal("0.30")),
        ),
    )

    targets = {target.symbol: target for target in plan.targets}
    assert targets["A"].constrained_weight == Decimal("0.15")
    assert targets["B"].constrained_weight == Decimal("0.15")
    assert "industry_budget" in targets["A"].reasons


def test_score_is_preserved_on_portfolio_target() -> None:
    plan = PortfolioBuilder().build(
        assets=[
            PortfolioAsset(
                "A",
                "stock",
                "Tech",
                Decimal("10"),
                0,
                Decimal("0.30"),
                score=Decimal("0.75"),
            ),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(),
    )

    assert plan.targets[0].score == Decimal("0.75")


class FakeStyleDataService:
    def __init__(
        self,
        style_exposures: dict[str, dict[str, Decimal]] | None = None,
        historical_prices: dict[str, list[Decimal]] | None = None,
        benchmark_prices: list[Decimal] | None = None,
    ) -> None:
        self.style_exposures = style_exposures or {}
        self.historical_prices = historical_prices or {}
        self.benchmark_prices = benchmark_prices

    def load_style_exposures(self, db, *, as_of_date, symbols, style_factor_map=None):
        return {
            style: {symbol: value for symbol, value in values.items() if symbol in symbols}
            for style, values in self.style_exposures.items()
        }

    def load_historical_prices(self, db, *, symbols, end_date, window_days=60, adjust_type="none"):
        return {s: self.historical_prices.get(s, []) for s in symbols}

    def load_benchmark_prices(
        self, db, *, benchmark_symbol, end_date, window_days=60, adjust_type="none"
    ):
        return self.benchmark_prices


def test_portfolio_builder_scales_weights_for_style_exposure_budget() -> None:
    style_exposures = {
        "momentum": {"A": Decimal("2"), "B": Decimal("-1")},
    }
    builder = PortfolioBuilder(
        style_data_service=FakeStyleDataService(
            style_exposures=style_exposures,
            historical_prices={
                "A": [Decimal("10")] * 61,
                "B": [Decimal("10")] * 61,
            },
            benchmark_prices=[Decimal("100")] * 61,
        )
    )

    plan = builder.build(
        assets=[
            # 提高 A 权重，使组合暴露超过预算
            PortfolioAsset("A", "stock", "Tech", Decimal("10"), 0, Decimal("0.50")),
            PortfolioAsset("B", "stock", "Tech", Decimal("10"), 0, Decimal("0.30")),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("1"),
            max_industry_weight=Decimal("1"),
            risk_budget=PortfolioRiskBudget(
                max_style_exposure=Decimal("0.50"),
                benchmark="000300.SH",
            ),
        ),
        db=object(),
        as_of_date=object(),
    )

    targets = {target.symbol: target for target in plan.targets}
    # 组合风格暴露 = 0.50*2 + 0.30*(-1) = 0.70；无基准因子，active=portfolio
    # 缩放比例 = 0.50 / 0.70 = 0.714...；缩放后 A=0.357, B=0.214
    # 之后行业预算把总量从 0.571 压回 0.40，等比例压缩
    assert targets["A"].constrained_weight == Decimal("0.25")
    assert targets["B"].constrained_weight == Decimal("0.15")
    assert "style_budget" in targets["A"].reasons
    assert plan.style_risk is not None
    assert plan.style_risk.tracking_error == Decimal("0")


def test_portfolio_builder_rejects_missing_benchmark_for_tracking_error() -> None:
    builder = PortfolioBuilder(
        style_data_service=FakeStyleDataService(
            style_exposures={},
            historical_prices={},
            benchmark_prices=None,
        )
    )

    with pytest.raises(ValueError, match="tracking error risk budget requires a benchmark"):
        builder.build(
            assets=[
                PortfolioAsset("A", "stock", "Tech", Decimal("10"), 0, Decimal("0.30")),
            ],
            cash=Decimal("100000"),
            constraints=PortfolioConstraints(
                cash_reserve_rate=Decimal("0"),
                max_symbol_weight=Decimal("1"),
                max_industry_weight=Decimal("1"),
                risk_budget=PortfolioRiskBudget(
                    max_tracking_error=Decimal("0.05"),
                ),
            ),
            db=object(),
            as_of_date=object(),
        )


def test_portfolio_builder_scales_weights_for_volatility_budget() -> None:
    builder = PortfolioBuilder(
        style_data_service=FakeStyleDataService(
            style_exposures={},
            historical_prices={
                "A": [Decimal("10")] * 30 + [Decimal("20")] * 31,
            },
            benchmark_prices=None,
        )
    )

    plan = builder.build(
        assets=[
            PortfolioAsset("A", "stock", "Tech", Decimal("10"), 0, Decimal("1.0")),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("1"),
            max_industry_weight=Decimal("1"),
            risk_budget=PortfolioRiskBudget(
                max_portfolio_volatility=Decimal("0.10"),
            ),
        ),
        db=object(),
        as_of_date=object(),
    )

    targets = {target.symbol: target for target in plan.targets}
    assert targets["A"].constrained_weight < Decimal("1.0")
    assert targets["A"].constrained_weight > Decimal("0")
    assert "volatility_budget" in targets["A"].reasons
    assert plan.style_risk is not None
    assert plan.style_risk.portfolio_volatility is not None
    assert plan.style_risk.portfolio_volatility <= Decimal("0.10")
