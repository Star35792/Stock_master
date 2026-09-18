from decimal import Decimal

from app.risk import OrderRiskInput, PreTradeRiskEngine, RiskLimits

LIMITS = RiskLimits(
    max_symbol_weight=Decimal("0.20"),
    max_industry_weight=Decimal("0.40"),
    max_drawdown=Decimal("0.10"),
    max_amount_participation=Decimal("0.10"),
)


def test_risk_engine_passes_order_within_limits() -> None:
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=100,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.10"),
            projected_symbol_weight=Decimal("0.15"),
            current_industry_weight=Decimal("0.20"),
            projected_industry_weight=Decimal("0.25"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.05"),
        ),
        limits=LIMITS,
    )

    assert decision.passed is True
    assert decision.rejection_reasons == ()


def test_risk_engine_rejects_risk_increasing_buy() -> None:
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=1000,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.10"),
            projected_symbol_weight=Decimal("0.30"),
            current_industry_weight=Decimal("0.30"),
            projected_industry_weight=Decimal("0.50"),
            average_daily_amount=Decimal("50000"),
            current_drawdown=Decimal("-0.12"),
        ),
        limits=LIMITS,
    )

    assert decision.passed is False
    assert decision.rejection_reasons == (
        "max_symbol_weight_exceeded",
        "max_industry_weight_exceeded",
        "drawdown_stop_triggered",
        "amount_capacity_exceeded",
    )


def test_risk_engine_allows_risk_reducing_sell_during_drawdown() -> None:
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=-100,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.30"),
            projected_symbol_weight=Decimal("0.25"),
            current_industry_weight=Decimal("0.50"),
            projected_industry_weight=Decimal("0.45"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.12"),
        ),
        limits=LIMITS,
    )

    assert decision.passed is True


def test_risk_engine_rejects_sell_that_does_not_reduce_risk_during_drawdown() -> None:
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=-100,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.15"),
            projected_symbol_weight=Decimal("0.15"),
            current_industry_weight=Decimal("0.25"),
            projected_industry_weight=Decimal("0.25"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.12"),
        ),
        limits=LIMITS,
    )

    assert decision.passed is False
    assert decision.rejection_reasons == ("drawdown_stop_triggered",)


def test_risk_engine_rejects_order_with_excessive_price_deviation() -> None:
    limits = RiskLimits(
        max_symbol_weight=Decimal("0.20"),
        max_industry_weight=Decimal("0.40"),
        max_drawdown=Decimal("0.10"),
        max_amount_participation=Decimal("0.10"),
        max_price_deviation=Decimal("0.02"),
    )
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=100,
            price=Decimal("10.50"),
            current_symbol_weight=Decimal("0.10"),
            projected_symbol_weight=Decimal("0.15"),
            current_industry_weight=Decimal("0.20"),
            projected_industry_weight=Decimal("0.25"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.05"),
            market_price=Decimal("10"),
        ),
        limits=limits,
    )

    assert decision.passed is False
    assert decision.rejection_reasons == ("price_deviation_exceeded",)
    assert decision.rules[-1].rule_name == "price_deviation"


def test_risk_engine_fails_closed_when_price_deviation_input_is_missing() -> None:
    limits = RiskLimits(
        max_symbol_weight=Decimal("0.20"),
        max_industry_weight=Decimal("0.40"),
        max_drawdown=Decimal("0.10"),
        max_amount_participation=Decimal("0.10"),
        max_price_deviation=Decimal("0.02"),
    )
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=100,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.10"),
            projected_symbol_weight=Decimal("0.15"),
            current_industry_weight=Decimal("0.20"),
            projected_industry_weight=Decimal("0.25"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.05"),
        ),
        limits=limits,
    )

    assert decision.rejection_reasons == ("price_deviation_data_missing",)


def test_risk_engine_rejects_order_above_absolute_amount_limit() -> None:
    limits = RiskLimits(
        max_symbol_weight=Decimal("0.20"),
        max_industry_weight=Decimal("0.40"),
        max_drawdown=Decimal("0.10"),
        max_amount_participation=Decimal("0.10"),
        max_order_amount=Decimal("1000"),
    )
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=110,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.10"),
            projected_symbol_weight=Decimal("0.15"),
            current_industry_weight=Decimal("0.20"),
            projected_industry_weight=Decimal("0.25"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.05"),
        ),
        limits=limits,
    )

    assert decision.passed is False
    assert decision.rejection_reasons == ("max_order_amount_exceeded",)
    assert decision.rules[-1].rule_name == "max_order_amount"


def test_risk_engine_rejects_risk_increase_above_cycle_turnover_limit() -> None:
    limits = RiskLimits(
        max_symbol_weight=Decimal("0.20"),
        max_industry_weight=Decimal("0.40"),
        max_drawdown=Decimal("0.10"),
        max_amount_participation=Decimal("0.10"),
        max_cycle_turnover=Decimal("0.20"),
    )
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=100,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.10"),
            projected_symbol_weight=Decimal("0.15"),
            current_industry_weight=Decimal("0.20"),
            projected_industry_weight=Decimal("0.25"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.05"),
            projected_cycle_turnover=Decimal("0.25"),
        ),
        limits=limits,
    )

    assert decision.passed is False
    assert decision.rejection_reasons == ("max_cycle_turnover_exceeded",)
    assert decision.rules[-1].rule_name == "max_cycle_turnover"


def test_risk_engine_allows_risk_reducing_sell_above_cycle_turnover_limit() -> None:
    limits = RiskLimits(
        max_symbol_weight=Decimal("0.20"),
        max_industry_weight=Decimal("0.40"),
        max_drawdown=Decimal("0.10"),
        max_amount_participation=Decimal("0.10"),
        max_cycle_turnover=Decimal("0.20"),
    )
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=-100,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.30"),
            projected_symbol_weight=Decimal("0.25"),
            current_industry_weight=Decimal("0.50"),
            projected_industry_weight=Decimal("0.45"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.12"),
            projected_cycle_turnover=Decimal("0.25"),
        ),
        limits=limits,
    )

    assert decision.passed is True
    assert decision.rejection_reasons == ()


def test_risk_engine_fails_closed_when_cycle_turnover_input_is_missing() -> None:
    limits = RiskLimits(
        max_symbol_weight=Decimal("0.20"),
        max_industry_weight=Decimal("0.40"),
        max_drawdown=Decimal("0.10"),
        max_amount_participation=Decimal("0.10"),
        max_cycle_turnover=Decimal("0.20"),
    )
    decision = PreTradeRiskEngine().check(
        order=OrderRiskInput(
            symbol="AAA",
            industry="Tech",
            order_quantity=100,
            price=Decimal("10"),
            current_symbol_weight=Decimal("0.10"),
            projected_symbol_weight=Decimal("0.15"),
            current_industry_weight=Decimal("0.20"),
            projected_industry_weight=Decimal("0.25"),
            average_daily_amount=Decimal("100000"),
            current_drawdown=Decimal("-0.05"),
        ),
        limits=limits,
    )

    assert decision.rejection_reasons == ("cycle_turnover_data_missing",)
