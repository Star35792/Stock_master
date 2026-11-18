from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.portfolio.style_risk import (
    build_portfolio_style_risk,
    compute_style_exposures,
    compute_tracking_error,
)


def test_missing_holding_date_rejects_portfolio_return_alignment() -> None:
    days = [date(2026, 1, 1) + timedelta(days=index) for index in range(61)]
    risk = build_portfolio_style_risk(
        weights={"A": Decimal("0.5"), "B": Decimal("0.5")},
        style_factors={},
        portfolio_prices={
            "A": {day: Decimal("10") for day in days},
            "B": {day: Decimal("20") for day in days if day != days[30]},
        },
        benchmark_prices={day: Decimal("100") for day in days},
    )

    assert risk.portfolio_volatility is None
    assert risk.tracking_error is None


def test_missing_benchmark_date_preserves_volatility_but_rejects_tracking_error() -> None:
    days = [date(2026, 1, 1) + timedelta(days=index) for index in range(61)]
    risk = build_portfolio_style_risk(
        weights={"A": Decimal("1")},
        style_factors={},
        portfolio_prices={
            "A": {day: Decimal("10") for day in days},
        },
        benchmark_prices={day: Decimal("100") for day in days if day != days[30]},
    )

    assert risk.portfolio_volatility is not None
    assert risk.tracking_error is None
    assert risk.missing_benchmark is True


def test_short_price_window_cannot_satisfy_risk_budget() -> None:
    days = [date(2026, 11, day) for day in (2, 3, 4)]
    risk = build_portfolio_style_risk(
        weights={"A": Decimal("1")},
        style_factors={},
        portfolio_prices={"A": {day: Decimal("10") for day in days}},
        benchmark_prices={day: Decimal("100") for day in days},
    )

    assert risk.portfolio_volatility is None
    assert risk.tracking_error is None


def test_tracking_error_rejects_different_return_window_lengths() -> None:
    assert compute_tracking_error(
        [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")],
        [Decimal("0.01"), Decimal("0.02")],
    ) is None


def test_missing_benchmark_factor_does_not_mislabel_absolute_style_as_active() -> None:
    exposures, missing = compute_style_exposures(
        weights={"A": Decimal("0.5")},
        style_factors={"momentum": {"A": Decimal("1")}},
        benchmark_weights={"BM": Decimal("1")},
    )
    assert missing == []
    assert exposures[0].portfolio_exposure == Decimal("0.5")
    assert exposures[0].benchmark_exposure is None
    assert exposures[0].active_exposure is None
def test_plan_request_rejects_values_beyond_stored_decimal_scale() -> None:
    from pydantic import ValidationError

    from app.schemas.portfolio import PortfolioPlanCreateRequest

    request = {
        "name": "precision",
        "as_of_date": "2026-11-13",
        "cash": "100.00001",
        "assets": [{
            "symbol": "600000.SH", "security_type": "stock", "industry": "银行",
            "price": "10.0000001", "current_quantity": 0,
            "requested_weight": "0.5",
        }],
    }
    with pytest.raises(ValidationError) as exc_info:
        PortfolioPlanCreateRequest.model_validate(request)
    assert {tuple(error["loc"]) for error in exc_info.value.errors()} == {
        ("cash",), ("assets", 0, "price"),
    }
