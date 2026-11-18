from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.dividend_scenario import DividendScenarioRequest
from app.services.dividend_scenario import DividendScenarioService

D = Decimal


def sample():
    return {
        "symbol": "A",
        "initial_cash": "1000",
        "initial_quantity": 100,
        "initial_price": "10",
        "prices": [
            {"trade_date": day, "close": price}
            for day, price in [
                ("2026-01-05", "10"),
                ("2026-01-06", "9.80"),
                ("2026-01-07", "9.80"),
                ("2026-01-08", "9.80"),
            ]
        ],
        "events": [
            {
                "event_id": "cash-1",
                "revision": "v1",
                "record_date": "2026-01-05",
                "ex_date": "2026-01-06",
                "payment_date": "2026-01-08",
                "cash_per_share": "0.20",
                "assumed_tax_rate": "0",
            }
        ],
        "fills": [],
    }


def evaluate(data):
    return DividendScenarioService().evaluate(DividendScenarioRequest.model_validate(data))


def fill(day="2026-01-07", side="sell", quantity=100, price="9.80", fee="0"):
    return dict(trade_date=day, side=side, quantity=quantity, price=price, fee=fee)


def test_known_answer_receivable_then_cash_and_input_unchanged():
    data = sample()
    original = deepcopy(data)
    report = evaluate(data)
    ex, paid = report.days[1], report.days[-1]
    assert (ex.cash, ex.receivable, ex.market_value, ex.total_equity) == (
        D(1000),
        D(20),
        D(980),
        D(2000),
    )
    assert (paid.cash, paid.receivable, paid.total_equity) == (D(1020), D(0), D(2000))
    assert report.total_return == 0
    assert data == original
    assert report.production_policy == "reject" and report.execution_eligible is False


def test_selling_before_payment_preserves_entitlement():
    data = sample()
    data["fills"] = [fill()]
    report = evaluate(data)
    assert (report.days[2].cash, report.days[2].quantity, report.days[2].receivable) == (
        D(1980),
        0,
        D(20),
    )
    assert report.days[-1].cash == D(2000)


def test_payment_after_window_retains_receivable_and_cannot_finance_buy():
    data = sample()
    data["events"][0]["payment_date"] = "2026-01-09"
    assert evaluate(data).days[-1].receivable == D(20)
    data["fills"] = [fill(side="buy", quantity=103)]
    with pytest.raises(ValueError, match="现金不足"):
        evaluate(data)


def test_assumed_tax_is_accrued_once_at_ex_and_payment_only_moves_assets():
    data = sample()
    data["events"][0]["assumed_tax_rate"] = "0.20"
    report = evaluate(data)
    assert (report.events[0].gross, report.events[0].assumed_tax, report.events[0].net) == (
        D(20),
        D(4),
        D(16),
    )
    assert report.days[1].receivable == 16
    assert report.days[1].total_equity == report.days[-1].total_equity == 1996
    assert report.days[-1].cash == 1016


def test_same_day_ex_payment_precedes_fills():
    data = sample()
    data["events"][0]["payment_date"] = "2026-01-06"
    data["fills"] = [fill("2026-01-06", "buy", 104)]
    day = evaluate(data).days[1]
    assert (day.cash, day.quantity, day.receivable, day.total_equity) == (
        D("0.80"),
        204,
        D(0),
        D(2000),
    )


@pytest.mark.parametrize(
    "trade_day,side,expected",
    [
        ("2026-01-05", "buy", 200),
        ("2026-01-05", "sell", 0),
        ("2026-01-06", "buy", 100),
    ],
)
def test_registration_close_determines_eligibility(trade_day, side, expected):
    data = sample()
    data["fills"] = [fill(trade_day, side, price="10")]
    assert evaluate(data).events[0].eligible_quantity == expected


def test_duplicate_event_idempotency_and_canonical_decimal_hash():
    data = sample()
    expected = evaluate(data)
    duplicate = deepcopy(data["events"][0])
    duplicate["cash_per_share"] = "0.2"
    data["events"] = [duplicate, *data["events"]]
    actual = evaluate(data)
    assert actual.duplicate_events_ignored == 1
    assert actual.final_equity == expected.final_equity
    assert actual.contract_sha256 == expected.contract_sha256
    data["events"][0]["revision"] = "v2"
    with pytest.raises(ValueError, match="冲突"):
        evaluate(data)


def test_multiple_events_zero_eligibility_and_rounding_conserve_amounts():
    data = sample()
    data["events"].append(
        {
            **data["events"][0],
            "event_id": "cash-2",
            "cash_per_share": "0.10005",
            "assumed_tax_rate": "0.2",
        }
    )
    report = evaluate(data)
    assert all(item.gross == item.assumed_tax + item.net for item in report.events)
    assert report.events[1].gross == D("10.01")
    assert report.events[1].assumed_tax == D("2.00")
    data["initial_quantity"] = 0
    assert all(item.net == 0 for item in evaluate(data).events)


@pytest.mark.parametrize("kind", ["registration", "missing_day", "oversell", "cash", "t1"])
def test_invalid_scenarios_fail_atomically(kind):
    data = sample()
    if kind == "registration":
        data["events"][0]["record_date"] = "2026-01-06"
    elif kind == "missing_day":
        data["prices"].pop(1)
    elif kind == "oversell":
        data["fills"] = [fill(quantity=101)]
    elif kind == "cash":
        data["fills"] = [fill(side="buy", quantity=1000), fill()]
    else:
        data["fills"] = [fill(side="buy", quantity=1), fill(quantity=101)]
    with pytest.raises(ValueError):
        evaluate(data)


@pytest.mark.parametrize("bad", [0.2, True, "NaN", "Infinity", "1e999999", "0.000000001"])
def test_decimal_limits_reject_nonfinite_float_bool_and_extreme_precision(bad):
    data = sample()
    data["events"][0]["assumed_tax_rate"] = bad
    with pytest.raises(ValidationError):
        evaluate(data)


def test_api_validates_and_serializes_decimal_strings_without_db_dependency():
    client = TestClient(app)
    result = client.post("/api/v1/strategy-research/cash-dividend-scenario", json=sample())
    assert result.status_code == 200
    assert D(result.json()["days"][1]["receivable"]) == 20
    invalid = sample()
    invalid["price_basis"] = "qfq"
    assert (
        client.post("/api/v1/strategy-research/cash-dividend-scenario", json=invalid).status_code
        == 422
    )
    invalid = sample()
    invalid["fills"] = [fill(quantity=1000)]
    assert (
        client.post("/api/v1/strategy-research/cash-dividend-scenario", json=invalid).status_code
        == 422
    )
