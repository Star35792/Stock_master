from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.price_value import PriceValueFees, PriceValueRequest
from app.strategies.price_value import calculate_price_metrics, calculate_valuation


def request_payload():
    scenario = {
        "cash_flows": ["100"],
        "terminal_cash_flow": "110",
        "cost_of_equity": "0.1",
        "terminal_growth": "0",
    }
    return {
        "symbol": "600000.SH",
        "trade_date": "2026-09-21",
        "valuation_at": "2026-09-21T16:00:00+08:00",
        "assumptions_available_at": "2026-09-20T16:00:00+08:00",
        "valid_until": "2026-09-30T16:00:00+08:00",
        "assumption_version": "test-v1",
        "assumptions_note": "User scenario only",
        "company_profile": "stable_non_financial",
        "diluted_shares": "10",
        "scenarios": {name: dict(scenario) for name in ("low", "base", "high")},
        "required_margin": "0.2",
        "quantity": 100,
        "fees": {
            "commission_rate": "0",
            "min_commission": "0",
            "transfer_fee_rate": "0",
            "exchange_fee_rate": "0",
            "slippage_bps": "0",
        },
    }


def test_fcfe_uses_explicit_next_year_cash_flow_and_year_end_discount():
    request = PriceValueRequest.model_validate(request_payload())
    values = calculate_valuation(request)
    assert values["low"] == Decimal("109.0909090909")
    assert values["low"] == values["base"] == values["high"]


def test_multiple_years_and_terminal_discount():
    payload = request_payload()
    for scenario in payload["scenarios"].values():
        scenario.update(cash_flows=["100", "121"], terminal_cash_flow="121", cost_of_equity="0.1")
    values = calculate_valuation(PriceValueRequest.model_validate(payload))
    assert values["low"] == Decimal("119.0909090909")


def test_rejects_actual_valuation_order_without_sorting_labels():
    payload = request_payload()
    payload["scenarios"]["low"]["terminal_cash_flow"] = "1000"
    with pytest.raises(ValueError, match="low <= base"):
        calculate_valuation(PriceValueRequest.model_validate(payload))


@pytest.mark.parametrize(
    "field,value",
    [
        ("diluted_shares", "NaN"),
        ("diluted_shares", "Infinity"),
        ("diluted_shares", "0"),
        ("diluted_shares", "1e100000"),
        ("diluted_shares", "0.000000001"),
        ("required_margin", "1"),
        ("required_margin", "0"),
        ("required_margin", "-0.1"),
        ("quantity", True),
        ("quantity", 0),
        ("valuation_at", "2026-09-21T16:00:00"),
        ("unexpected", True),
    ],
)
def test_request_boundary(field, value):
    payload = request_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        PriceValueRequest.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("cash_flows", []),
        ("cash_flows", ["1"] * 11),
        ("cash_flows", ["-1"]),
        ("terminal_cash_flow", "0"),
        ("cost_of_equity", "0"),
        ("terminal_growth", "0.1"),
        ("terminal_growth", "-1"),
        ("terminal_growth", "Infinity"),
        ("unverified_flag", True),
    ],
)
def test_scenario_boundary(field, value):
    payload = request_payload()
    payload["scenarios"]["low"][field] = value
    with pytest.raises(ValidationError):
        PriceValueRequest.model_validate(payload)


def test_zero_fees_stay_zero_and_margin_equality_qualifies():
    request = PriceValueRequest.model_validate(request_payload())
    metrics = calculate_price_metrics(
        Decimal("80"), Decimal("100"), Decimal("0.2"), 100, request.fees
    )
    assert metrics["price_condition_met"] is True
    assert metrics["fee_total"] == 0
    assert metrics["max_buy_quote"] == 80
    assert metrics["cost_adjusted_margin"] == Decimal("0.2")


def test_minimum_commission_and_slippage_count_exactly_once():
    fees = PriceValueFees(**dict(request_payload()["fees"], min_commission="5", slippage_bps="100"))
    metrics = calculate_price_metrics(Decimal("80"), Decimal("100"), Decimal("0.2"), 100, fees)
    assert metrics["all_in_price"] == Decimal("80.85")
    assert metrics["fee_total"] == 5
    assert metrics["slippage_cost"] == 80
    assert metrics["price_condition_met"] is False
    assert metrics["max_buy_quote"] == Decimal("79.15")


def test_rounded_quote_ceiling_satisfies_engine_and_next_tick_fails():
    fees = PriceValueFees(
        **dict(request_payload()["fees"], commission_rate="0.001", transfer_fee_rate="0.00001")
    )
    metrics = calculate_price_metrics(Decimal("80"), Decimal("100"), Decimal("0.2"), 100, fees)
    maximum = metrics["max_buy_quote"]
    assert calculate_price_metrics(maximum, Decimal("100"), Decimal("0.2"), 100, fees)[
        "price_condition_met"
    ]
    assert not calculate_price_metrics(
        maximum + Decimal("0.01"), Decimal("100"), Decimal("0.2"), 100, fees
    )["price_condition_met"]


def test_no_positive_buy_quote_when_minimum_fee_exceeds_budget():
    fees = PriceValueFees(**dict(request_payload()["fees"], min_commission="100"))
    metrics = calculate_price_metrics(Decimal("1"), Decimal("1"), Decimal("0.2"), 1, fees)
    assert metrics["max_buy_quote"] == 0
    assert metrics["price_condition_met"] is False


def test_each_fee_must_be_explicit():
    payload = request_payload()
    del payload["fees"]["commission_rate"]
    with pytest.raises(ValidationError):
        PriceValueRequest.model_validate(payload)


def test_constant_fcfe_exact_margin_boundary_uses_published_value():
    payload = request_payload()
    for scenario in payload["scenarios"].values():
        scenario.update(cash_flows=["10"], terminal_cash_flow="10")
    request = PriceValueRequest.model_validate(payload)
    values = calculate_valuation(request)
    assert values["low"] == Decimal("10.0000000000")
    metrics = calculate_price_metrics(
        Decimal("7"), values["low"], Decimal("0.3"), 100, request.fees
    )
    assert metrics["price_condition_met"] is True
    assert metrics["max_buy_quote"] == Decimal("7")


def test_value_rounded_to_zero_is_rejected():
    payload = request_payload()
    payload["diluted_shares"] = "1000000000000000000"
    with pytest.raises(ValueError, match="positive at ten-decimal precision"):
        calculate_valuation(PriceValueRequest.model_validate(payload))


def test_zero_margin_is_rejected_by_kernel():
    request = PriceValueRequest.model_validate(request_payload())
    with pytest.raises(ValueError, match="margin must be"):
        calculate_price_metrics(Decimal("7"), Decimal("10"), Decimal("0"), 100, request.fees)
