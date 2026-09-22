from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtest.price_value import run_price_value_backtest
from app.schemas.price_value_backtest import PriceValueBacktestRequest

DAYS = [date(2026, 1, 5) + timedelta(days=index) for index in range(5)]


def _assumption(**updates):
    def scenario(amount):
        return {
            "cash_flows": [amount], "terminal_cash_flow": amount,
            "cost_of_equity": ".1", "terminal_growth": "0",
        }

    return {
        "version": "v1", "valuation_at": "2026-01-04T16:00:00+08:00",
        "available_at": "2026-01-04T16:00:00+08:00",
        "valid_until": "2026-01-31T16:00:00+08:00", "source_reference": "test assumptions",
        "diluted_shares": "1",
        "scenarios": {"low": scenario("1"), "base": scenario("1.2"), "high": scenario("1.5")},
        **updates,
    }


def _payload(**updates):
    return PriceValueBacktestRequest.model_validate({
        "name": "Replay", "symbol": "600000.SH", "start_date": DAYS[0], "end_date": DAYS[-1],
        "initial_cash": "10000", "position_weight": "1", "required_margin": ".3",
        "max_participation_rate": ".1", "sell_stamp_duty_rate": "0",
        "fees": {
            "commission_rate": "0", "min_commission": "0", "transfer_fee_rate": "0",
            "exchange_fee_rate": "0", "slippage_bps": "0",
        },
        "assumptions": [_assumption()], **updates,
    })


def _bar(day, price="6", **updates):
    price = Decimal(price)
    row = {
        "symbol": "600000.SH", "trade_date": day, "open": price, "close": price,
        "high": price, "low": price, "volume": Decimal("100000"),
        "limit_up": Decimal("100"), "limit_down": Decimal(".01"), "is_suspended": False,
    }
    row.update(updates)
    row["high"] = max(row["high"], row["open"], row["close"])
    row["low"] = min(row["low"], row["open"], row["close"])
    return SimpleNamespace(**row)


def _run(payload=None, bars=None):
    return run_price_value_backtest(
        payload or _payload(), bars or [_bar(day) for day in DAYS], DAYS,
    )


def test_close_signal_fills_next_open_and_keeps_cash_without_top_up():
    bars = [_bar(DAYS[0]), *[_bar(day, "5") for day in DAYS[1:]]]
    result, decisions = _run(bars=bars)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert (trade.signal_date, trade.trade_date, trade.quantity) == (DAYS[0], DAYS[1], 2000)
    assert result.equity_curve[0].market_value == 0
    assert len([item for item in decisions if item.reason == "existing_position_no_top_up"]) == 4


def test_unmet_price_condition_stays_in_cash_and_never_creates_final_day_order():
    result, decisions = _run(bars=[_bar(day, "8") for day in DAYS])
    assert not result.trades and result.final_equity == Decimal("10000")
    assert decisions[-1].reason == "end_of_window"
    assert all(row.cash == result.initial_cash for row in result.equity_curve)


def test_gap_up_is_rechecked_at_actual_fill_price():
    result, decisions = _run(bars=[_bar(DAYS[0]), *[_bar(day, "8") for day in DAYS[1:]]])
    assert not result.trades
    assert any(item.phase == "open" and item.reason == "all_in_price_above_ceiling"
               for item in decisions)


def test_minimum_commission_checks_actual_lot_and_respects_budget():
    payload = _payload()
    payload.fees.min_commission = Decimal("5")
    result, _ = _run(payload)
    assert result.trades[0].quantity == 1600
    assert result.trades[0].cash_after == Decimal("395")
    assert result.trades[0].commission == Decimal("5")


def test_minimum_commission_cannot_cross_price_ceiling():
    payload = _payload()
    payload.fees.min_commission = Decimal("5")
    bars = [_bar(DAYS[0], "6.99"), *[_bar(day, "7") for day in DAYS[1:]]]
    result, decisions = _run(payload, bars)
    assert not result.trades
    assert any(item.phase == "open" and item.reason == "all_in_price_above_ceiling"
               for item in decisions)


def test_explicit_zero_fees_are_preserved():
    result, _ = _run()
    assert result.trades[0].commission == 0
    assert result.trades[0].fill_price == 6
    assert result.trades[0].cash_after == 400


def test_position_weight_limits_total_cash_cost():
    result, _ = _run(_payload(position_weight=".2"))
    assert result.trades[0].quantity == 300
    assert result.trades[0].cash_after == 8200


def test_reaching_base_creates_following_session_exit():
    result, _ = _run(bars=[_bar(DAYS[0]), _bar(DAYS[1]), *[_bar(day, "12") for day in DAYS[2:]]])
    assert [(item.side, item.trade_date) for item in result.trades] == [
        ("buy", DAYS[1]), ("sell", DAYS[3]),
    ]
    assert result.trades[1].signal_date == DAYS[2]
    assert result.final_equity == Decimal("19600")


def test_partial_exit_and_suspension_keep_original_exit_intent():
    bars = [
        _bar(DAYS[0]),
        _bar(DAYS[1], close=Decimal("12"), volume=Decimal("1000")),
        _bar(DAYS[2], "12", volume=Decimal("1000")),
        _bar(DAYS[3], "12", is_suspended=True),
        _bar(DAYS[4], "12"),
    ]
    result, decisions = _run(bars=bars)
    assert [(item.side, item.quantity) for item in result.trades] == [
        ("buy", 1600), ("sell", 100), ("sell", 1500),
    ]
    assert all(item.signal_date == DAYS[1] for item in result.trades[1:])
    assert any(item.reason == "suspended" and item.phase == "open" for item in decisions)


def test_buy_capacity_uses_previous_volume_only():
    bars = [_bar(DAYS[0], volume=Decimal("2000")),
            _bar(DAYS[1], volume=Decimal("999999999")), *[_bar(day) for day in DAYS[2:]]]
    result, _ = _run(bars=bars)
    assert result.trades[0].quantity == 200


def test_new_assumption_before_open_cancels_old_buy():
    payload = _payload(assumptions=[_assumption(), _assumption(
        version="v2", valuation_at="2026-01-06T09:00:00+08:00",
        available_at="2026-01-06T09:00:00+08:00", thesis_broken=True,
    )])
    result, decisions = _run(payload)
    assert not result.trades
    assert any(item.reason == "assumption_revised_before_open" for item in decisions)


def test_future_revision_cannot_change_earlier_entry_or_signal():
    payload = _payload(assumptions=[_assumption(), _assumption(
        version="v2", valuation_at="2026-01-07T16:00:00+08:00",
        available_at="2026-01-07T16:00:00+08:00", thesis_broken=True,
    )])
    result, decisions = _run(payload)
    assert [(trade.side, trade.trade_date) for trade in result.trades] == [
        ("buy", DAYS[1]), ("sell", DAYS[3]),
    ]
    assert not any(item.assumption_version == "v2" and item.trade_date < DAYS[2]
                   for item in decisions)


def test_expired_latest_version_never_falls_back_to_old_valid_assumption():
    payload = _payload(assumptions=[_assumption(), _assumption(
        version="v2", valuation_at="2026-01-05T10:00:00+08:00",
        available_at="2026-01-05T10:00:00+08:00", valid_until="2026-01-05T11:00:00+08:00",
    )])
    result, decisions = _run(payload)
    assert not result.trades
    assert all(item.reason == "assumption_expired" for item in decisions)
    assert all(item.assumption_version == "v2" for item in decisions)


def test_assumption_expiring_overnight_cancels_buy():
    payload = _payload(assumptions=[_assumption(valid_until="2026-01-06T09:00:00+08:00")])
    result, decisions = _run(payload)
    assert not result.trades
    assert any(item.reason == "assumption_expired_before_open" for item in decisions)


def test_expired_held_assumption_requests_review_without_automatic_sale():
    payload = _payload(assumptions=[_assumption(valid_until="2026-01-06T10:00:00+08:00")])
    result, decisions = _run(payload)
    assert [trade.side for trade in result.trades] == ["buy"]
    assert decisions[-1].action == "review"
    assert decisions[-1].reason == "assumption_expired"


def test_thesis_break_event_survives_expired_valuation_for_existing_position():
    payload = _payload(assumptions=[_assumption(), _assumption(
        version="v2", valuation_at="2026-01-07T10:00:00+08:00",
        available_at="2026-01-07T10:00:00+08:00",
        valid_until="2026-01-07T12:00:00+08:00", thesis_broken=True,
    )])
    result, decisions = _run(payload)
    assert [(trade.side, trade.trade_date) for trade in result.trades] == [
        ("buy", DAYS[1]), ("sell", DAYS[3]),
    ]
    assert any(item.trade_date == DAYS[2] and item.reason == "thesis_broken"
               and item.action == "sell_signal" for item in decisions)


@pytest.mark.parametrize("updates,reason", [
    ({"limit_up": None}, "price_limits_missing"),
    ({"limit_up": Decimal("6")}, "limit_up"),
    ({"is_suspended": True}, "suspended"),
])
def test_untradable_next_open_does_not_fill(updates, reason):
    bars = [_bar(DAYS[0]), *[_bar(day, **updates) for day in DAYS[1:]]]
    result, decisions = _run(bars=bars)
    assert not result.trades
    assert any(item.reason == reason and item.phase == "open" for item in decisions)


def test_slippage_cannot_fill_above_price_limit():
    payload = _payload()
    payload.fees.slippage_bps = Decimal("20")
    bars = [_bar(DAYS[0]), *[_bar(day, limit_up=Decimal("6.01")) for day in DAYS[1:]]]
    result, decisions = _run(payload, bars)
    assert not result.trades
    assert any(item.reason == "fill_outside_price_limits" for item in decisions)


def test_unaffordable_exit_costs_never_create_negative_cash():
    payload = _payload(sell_stamp_duty_rate="1")
    payload.fees.min_commission = Decimal("1000")
    bars = [_bar(DAYS[0]), _bar(DAYS[1], close=Decimal("12")),
            *[_bar(day, "12") for day in DAYS[2:]]]
    result, decisions = _run(payload, bars)
    assert [trade.side for trade in result.trades] == ["buy"]
    assert all(item.cash >= 0 for item in result.equity_curve)
    assert any(item.reason == "insufficient_cash_for_exit_fees" for item in decisions)


@pytest.mark.parametrize("problem", ["missing", "duplicate", "nonfinite", "wrong_symbol"])
def test_invalid_market_slice_is_rejected(problem):
    bars = [_bar(day) for day in DAYS]
    if problem == "missing":
        bars.pop()
    elif problem == "duplicate":
        bars.append(bars[0])
    elif problem == "nonfinite":
        bars[0].close = Decimal("NaN")
    else:
        bars[0].symbol = "000001.SZ"
    with pytest.raises(ValueError):
        _run(bars=bars)


def test_input_order_does_not_change_result():
    payload = _payload()
    bars = [_bar(day) for day in DAYS]
    expected = run_price_value_backtest(payload, bars, DAYS)
    assert run_price_value_backtest(payload, list(reversed(bars)), list(reversed(DAYS))) == expected


def test_validity_is_inclusive_at_open_and_close_cutoffs():
    payload = _payload(assumptions=[_assumption(valid_until="2026-01-06T09:30:00+08:00")])
    result, _ = _run(payload)
    assert result.trades[0].trade_date == DAYS[1]
    assert payload.assumptions[0].valid_until == datetime.fromisoformat("2026-01-06T09:30:00+08:00")
