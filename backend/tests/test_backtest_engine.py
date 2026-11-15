from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtest import AShareCostFeeModel, DailyEventBacktestEngine, TargetWeightSignal
from app.backtest.engine import ENGINE_VERSION


def _bar(trade_date: date, symbol: str, open_: str, close: str) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=trade_date,
        symbol=symbol,
        open=Decimal(open_),
        close=Decimal(close),
    )


def _calendar(*symbols: str) -> dict[str, list[date]]:
    return {
        symbol: [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
        for symbol in symbols
    }


@pytest.mark.parametrize("cash,expected_quantity", [
    ("10000", 900), ("10005", 1000), ("1004", 0), ("1005", 100),
])
def test_affordable_quantity_charges_minimum_commission_once_per_order(
    cash, expected_quantity,
) -> None:
    fee_model = AShareCostFeeModel(
        commission_rate=Decimal("0"), min_commission=Decimal("5"),
        stamp_duty_rate=Decimal("0"), transfer_fee_rate=Decimal("0"),
        exchange_fee_rate=Decimal("0"), slippage_bps=Decimal("0"),
    )
    result = DailyEventBacktestEngine(initial_cash=Decimal(cash), fee_model=fee_model).run(
        bars=[_bar(day, "AAA", "10", "10") for day in _calendar("AAA")["AAA"]],
        signals=[TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("1"))],
        execution_calendar=_calendar("AAA"),
    )
    assert sum(trade.quantity for trade in result.trades) == expected_quantity
    assert all(snapshot.cash >= 0 for snapshot in result.equity_curve)


def test_backtest_executes_signal_on_next_available_open() -> None:
    bars = [
        _bar(date(2026, 7, 1), "AAA", "10", "10"),
        _bar(date(2026, 7, 2), "AAA", "11", "12"),
        _bar(date(2026, 7, 3), "AAA", "13", "13"),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
    )

    result = engine.run(
        bars=bars,
        signals=[
            TargetWeightSignal(
                signal_date=date(2026, 7, 1),
                symbol="AAA",
                target_weight=Decimal("0.5"),
            )
        ],
        execution_calendar=_calendar("AAA"),
    )

    assert len(result.trades) == 1
    assert result.trades[0].signal_date == date(2026, 7, 1)
    assert result.trades[0].trade_date == date(2026, 7, 2)
    assert result.trades[0].quantity == 4500
    assert result.equity_curve[0].total_equity == Decimal("100000")
    assert result.final_equity == Decimal("109000")
    assert result.total_return == Decimal("0.09")


def test_backtest_rejects_missing_held_bar_without_pending_order() -> None:
    days = _calendar("AAA")["AAA"]
    with pytest.raises(ValueError, match="missing trusted held-position bar.*2026-07-03.*AAA"):
        DailyEventBacktestEngine(initial_cash=Decimal("10000")).run(
            bars=[_bar(day, "AAA", "10", "10") for day in days[:2]],
            signals=[TargetWeightSignal(days[0], "AAA", Decimal("1"))],
            execution_calendar=_calendar("AAA"),
        )


def test_backtest_allows_missing_bar_when_symbol_exchange_is_closed() -> None:
    days = _calendar("AAA")["AAA"]
    result = DailyEventBacktestEngine(initial_cash=Decimal("10000")).run(
        bars=[_bar(day, "AAA", "10", "10") for day in days[:2]],
        signals=[TargetWeightSignal(days[0], "AAA", Decimal("1"))],
        execution_calendar={"AAA": days[:2], "BBB": days},
    )
    assert result.equity_curve[-1].trade_date == days[2]


def test_backtest_rejects_missing_bar_on_scheduled_trade_date() -> None:
    bars = [
        _bar(date(2026, 7, 1), "AAA", "10", "10"),
        _bar(date(2026, 7, 2), "BBB", "20", "20"),
        _bar(date(2026, 7, 3), "AAA", "10", "10"),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0.001"),
        slippage_bps=Decimal("10"),
        lot_size=100,
    )

    with pytest.raises(ValueError, match="missing trusted execution bar"):
        engine.run(
            bars=bars,
            signals=[
                TargetWeightSignal(
                    signal_date=date(2026, 7, 1),
                    symbol="AAA",
                    target_weight=Decimal("1"),
                )
            ],
            execution_calendar={
                "AAA": [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
            },
        )


def test_backtest_applies_costs_on_next_scheduled_open() -> None:
    bars = [
        _bar(date(2026, 7, 1), "AAA", "10", "10"),
        _bar(date(2026, 7, 2), "AAA", "10", "10"),
        _bar(date(2026, 7, 3), "AAA", "10", "10"),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0.001"),
        slippage_bps=Decimal("10"),
        lot_size=100,
    )

    result = engine.run(
        bars=bars,
        signals=[TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("1"))],
        execution_calendar=_calendar("AAA"),
    )

    assert result.trades[0].trade_date == date(2026, 7, 2)
    assert result.trades[0].fill_price == Decimal("10.010")
    assert result.trades[0].quantity == 900
    assert result.trades[0].commission == Decimal("9.009000")
    assert result.final_equity < Decimal("10000")


def test_backtest_sells_before_buying_on_rebalance() -> None:
    bars = [
        _bar(date(2026, 7, 1), "AAA", "10", "10"),
        _bar(date(2026, 7, 1), "BBB", "10", "10"),
        _bar(date(2026, 7, 2), "AAA", "10", "10"),
        _bar(date(2026, 7, 2), "BBB", "10", "10"),
        _bar(date(2026, 7, 3), "AAA", "10", "10"),
        _bar(date(2026, 7, 3), "BBB", "10", "10"),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
    )

    result = engine.run(
        bars=bars,
        signals=[
            TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("1")),
            TargetWeightSignal(date(2026, 7, 2), "AAA", Decimal("0")),
            TargetWeightSignal(date(2026, 7, 2), "BBB", Decimal("1")),
        ],
        execution_calendar=_calendar("AAA", "BBB"),
    )

    day_three_trades = [
        trade for trade in result.trades if trade.trade_date == date(2026, 7, 3)
    ]
    assert [trade.side for trade in day_three_trades] == ["sell", "buy"]
    assert day_three_trades[1].quantity == 1000


def test_backtest_rejects_invalid_daily_weights() -> None:
    engine = DailyEventBacktestEngine(initial_cash=Decimal("10000"))

    with pytest.raises(ValueError, match="exceed"):
        engine.run(
            bars=[],
            signals=[
                TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("0.6")),
                TargetWeightSignal(date(2026, 7, 1), "BBB", Decimal("0.5")),
            ],
            execution_calendar=_calendar("AAA", "BBB"),
        )


def test_backtest_ordering_is_independent_of_signal_and_bar_order() -> None:
    bars = [
        _bar(date(2026, 7, 1), "AAA", "10", "10"),
        _bar(date(2026, 7, 1), "BBB", "10", "10"),
        _bar(date(2026, 7, 2), "AAA", "10", "11"),
        _bar(date(2026, 7, 2), "BBB", "10", "9"),
        _bar(date(2026, 7, 3), "AAA", "11", "11"),
        _bar(date(2026, 7, 3), "BBB", "9", "9"),
    ]
    signals = [
        TargetWeightSignal(date(2026, 7, 1), "BBB", Decimal("0.5")),
        TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("0.5")),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0.001"),
        slippage_bps=Decimal("2"),
        lot_size=100,
    )

    first = engine.run(
        bars=bars,
        signals=signals,
        execution_calendar=_calendar("AAA", "BBB"),
    )
    second = engine.run(
        bars=list(reversed(bars)),
        signals=list(reversed(signals)),
        execution_calendar=_calendar("AAA", "BBB"),
    )

    assert first == second
    assert [trade.symbol for trade in first.trades] == ["AAA", "BBB"]


def test_backtest_rejects_duplicate_bars_and_signals() -> None:
    bar = _bar(date(2026, 7, 1), "AAA", "10", "10")
    signal = TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("0.5"))
    engine = DailyEventBacktestEngine(initial_cash=Decimal("10000"))

    with pytest.raises(ValueError, match="duplicate market bar"):
        engine.run(
            bars=[bar, bar],
            signals=[signal],
            execution_calendar=_calendar("AAA"),
        )
    with pytest.raises(ValueError, match="duplicate target signal"):
        engine.run(
            bars=[bar],
            signals=[signal, signal],
            execution_calendar=_calendar("AAA"),
        )


def test_backtest_requires_a_calendar_for_every_signal_symbol() -> None:
    engine = DailyEventBacktestEngine(initial_cash=Decimal("10000"))

    with pytest.raises(ValueError, match="required for every signal symbol"):
        engine.run(
            bars=[_bar(date(2026, 7, 1), "AAA", "10", "10")],
            signals=[TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("1"))],
            execution_calendar={},
        )


def test_backtest_a_share_cost_model_charges_stamp_duty_on_sell() -> None:
    from app.backtest import AShareCostFeeModel

    bars = [
        _bar(date(2026, 7, 1), "AAA", "10", "10"),
        _bar(date(2026, 7, 2), "AAA", "10", "10"),
        _bar(date(2026, 7, 3), "AAA", "10", "10"),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"),
        lot_size=100,
        fee_model=AShareCostFeeModel(
            commission_rate=Decimal("0"),
            min_commission=Decimal("0"),
            stamp_duty_rate=Decimal("0.0005"),
            transfer_fee_rate=Decimal("0"),
            exchange_fee_rate=Decimal("0"),
            slippage_bps=Decimal("0"),
        ),
    )

    result = engine.run(
        bars=bars,
        signals=[
            TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("1")),
            TargetWeightSignal(date(2026, 7, 2), "AAA", Decimal("0")),
        ],
        execution_calendar=_calendar("AAA"),
    )

    buy_trade = [t for t in result.trades if t.side == "buy"][0]
    sell_trade = [t for t in result.trades if t.side == "sell"][0]
    assert buy_trade.commission == 0
    assert sell_trade.commission == sell_trade.quantity * sell_trade.fill_price * Decimal("0.0005")


def _tradable_bar(
    trade_date: date,
    symbol: str,
    open_: str,
    close: str,
    limit_up: str | None = None,
    limit_down: str | None = None,
    is_suspended: bool = False,
) -> SimpleNamespace:
    bar = _bar(trade_date, symbol, open_, close)
    bar.limit_up = Decimal(limit_up) if limit_up is not None else None
    bar.limit_down = Decimal(limit_down) if limit_down is not None else None
    bar.is_suspended = is_suspended
    return bar


def test_backtest_blocks_buy_at_limit_up() -> None:
    bars = [
        _tradable_bar(date(2026, 7, 1), "AAA", "10", "10"),
        _tradable_bar(date(2026, 7, 2), "AAA", "11", "11", limit_up="11"),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
    )

    result = engine.run(
        bars=bars,
        signals=[TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("1"))],
        execution_calendar=_calendar("AAA"),
    )

    assert all(trade.side == "sell" for trade in result.trades) or not result.trades
    buy_trades = [trade for trade in result.trades if trade.side == "buy"]
    assert buy_trades == []


def test_backtest_blocks_sell_at_limit_down() -> None:
    bars = [
        _tradable_bar(date(2026, 7, 1), "AAA", "10", "10"),
        _tradable_bar(date(2026, 7, 2), "AAA", "11", "11"),
        _tradable_bar(date(2026, 7, 3), "AAA", "9", "9", limit_down="9"),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
    )

    result = engine.run(
        bars=bars,
        signals=[
            TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("1")),
            TargetWeightSignal(date(2026, 7, 2), "AAA", Decimal("0")),
        ],
        execution_calendar=_calendar("AAA"),
    )

    sell_trades = [trade for trade in result.trades if trade.side == "sell"]
    assert sell_trades == []


def test_backtest_blocks_all_trades_when_suspended() -> None:
    bars = [
        _tradable_bar(date(2026, 7, 1), "AAA", "10", "10"),
        _tradable_bar(
            date(2026, 7, 2), "AAA", "10", "10", is_suspended=True
        ),
        _tradable_bar(date(2026, 7, 3), "AAA", "10", "10"),
    ]
    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
    )

    result = engine.run(
        bars=bars,
        signals=[
            TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("1")),
            TargetWeightSignal(date(2026, 7, 2), "AAA", Decimal("0")),
        ],
        execution_calendar=_calendar("AAA"),
    )

    assert not any(trade.trade_date == date(2026, 7, 2) for trade in result.trades)


@pytest.mark.parametrize("blocked_bar", [
    {"limit_down": "9"}, {"is_suspended": True},
], ids=["limit_down", "explicit_suspended_input"])
def test_exit_target_retries_when_bar_is_explicitly_untradable(blocked_bar) -> None:
    # Production trusted data omits real suspension days.  The explicit
    # suspended bar exercises the policy boundary only; a missing held bar is
    # covered separately and remains fail-closed.
    days = [date(2026, 7, day) for day in (1, 2, 3, 6)]
    bars = [
        _tradable_bar(days[0], "AAA", "10", "10"),
        _tradable_bar(days[1], "AAA", "10", "10"),
        _tradable_bar(days[2], "AAA", "9", "9", **blocked_bar),
        _tradable_bar(days[3], "AAA", "8", "8"),
    ]
    result = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"), commission_rate=Decimal(),
        slippage_bps=Decimal(),
    ).run(
        bars=bars,
        signals=[
            TargetWeightSignal(days[0], "AAA", Decimal("1")),
            TargetWeightSignal(days[1], "AAA", Decimal()),
        ],
        execution_calendar={"AAA": days},
    )

    assert ENGINE_VERSION == "daily_event_v4"
    assert [(trade.side, trade.signal_date, trade.trade_date) for trade in result.trades] == [
        ("buy", days[0], days[1]), ("sell", days[1], days[3]),
    ]
    blocked = result.execution_events
    assert len(blocked) == 1
    assert blocked[0].status == "retry_scheduled"
    assert blocked[0].trade_date == days[2]
    assert blocked[0].next_execution_date == days[3]
    assert blocked[0].reason
    assert result.positions[-1].trade_date == days[2]


@pytest.mark.parametrize("retry_price, expected_sell", [
    ("8", True), ("2", False),
])
def test_blocked_partial_sell_retries_without_reversing_to_buy(
    retry_price, expected_sell
) -> None:
    days = [date(2026, 7, day) for day in (1, 2, 3, 6)]
    result = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"), commission_rate=Decimal(),
        slippage_bps=Decimal(),
    ).run(
        bars=[
            _tradable_bar(days[0], "AAA", "10", "10"),
            _tradable_bar(days[1], "AAA", "10", "10"),
            _tradable_bar(days[2], "AAA", "9", "9", limit_down="9"),
            _tradable_bar(days[3], "AAA", retry_price, retry_price),
        ],
        signals=[
            TargetWeightSignal(days[0], "AAA", Decimal("0.5")),
            TargetWeightSignal(days[1], "AAA", Decimal("0.4")),
        ],
        execution_calendar={"AAA": days},
    )
    assert result.execution_events[0].status == "retry_scheduled"
    later_trades = [trade for trade in result.trades if trade.trade_date == days[3]]
    if expected_sell:
        assert len(later_trades) == 1
        assert later_trades[0].side == "sell"
        assert later_trades[0].signal_date == days[1]
    else:
        assert later_trades == []
        assert result.execution_events[-1].status == "no_longer_needed"


def test_newer_target_supersedes_blocked_exit() -> None:
    days = [date(2026, 7, day) for day in (1, 2, 3, 6, 7)]
    bars = [
        _tradable_bar(days[0], "AAA", "10", "10"),
        _tradable_bar(days[1], "AAA", "10", "10"),
        _tradable_bar(days[2], "AAA", "9", "9", limit_down="9"),
        _tradable_bar(days[3], "AAA", "10", "10"),
        _tradable_bar(days[4], "AAA", "10", "10"),
    ]
    result = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"), commission_rate=Decimal(),
        slippage_bps=Decimal(),
    ).run(
        bars=bars,
        signals=[
            TargetWeightSignal(days[0], "AAA", Decimal("1")),
            TargetWeightSignal(days[1], "AAA", Decimal()),
            TargetWeightSignal(days[2], "AAA", Decimal("0.5")),
        ],
        execution_calendar={"AAA": days},
    )

    sells = [trade for trade in result.trades if trade.side == "sell"]
    assert len(sells) == 1
    assert sells[0].signal_date == days[2]
    assert sells[0].trade_date == days[3]
    assert sells[0].quantity == 5000
    assert [event.status for event in result.execution_events] == [
        "retry_scheduled", "superseded",
    ]


def test_blocked_exit_on_last_open_date_is_reported_without_fabricated_fill() -> None:
    days = [date(2026, 7, day) for day in (1, 2, 3)]
    result = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"), commission_rate=Decimal(),
        slippage_bps=Decimal(),
    ).run(
        bars=[
            _tradable_bar(days[0], "AAA", "10", "10"),
            _tradable_bar(days[1], "AAA", "10", "10"),
            _tradable_bar(days[2], "AAA", "9", "9", limit_down="9"),
        ],
        signals=[
            TargetWeightSignal(days[0], "AAA", Decimal("1")),
            TargetWeightSignal(days[1], "AAA", Decimal()),
        ],
        execution_calendar={"AAA": days},
    )
    assert [trade.side for trade in result.trades] == ["buy"]
    assert [(event.status, event.next_execution_date) for event in result.execution_events] == [
        ("blocked_final", None),
    ]
    assert result.positions[-1].quantity == 10000


def _run_with_constraints(
    *,
    symbols_weights: list[tuple[str, str, str]],
    constraints_kwargs: dict,
    profiles: dict[str, tuple[str, str]] | None = None,
):
    """Run a one-shot rebalance (signal 7/1 -> execute 7/2) with constraints.

    ``symbols_weights`` entries are (symbol, open price, requested weight).
    """
    symbols = [symbol for symbol, _, _ in symbols_weights]
    bars = []
    for trade_day in _calendar(*symbols)[symbols[0]]:
        for symbol, open_, _ in symbols_weights:
            bars.append(_bar(trade_day, symbol, open_, open_))
    signals = [
        TargetWeightSignal(date(2026, 7, 1), symbol, Decimal(weight))
        for symbol, _, weight in symbols_weights
    ]
    from app.portfolio import PortfolioConstraints

    engine = DailyEventBacktestEngine(
        initial_cash=Decimal("100000"),
        portfolio_constraints=PortfolioConstraints(**constraints_kwargs),
        security_profiles=profiles or {
            symbol: ("stock", "银行") for symbol, _, _ in symbols_weights
        },
    )
    return engine.run(bars=bars, signals=signals, execution_calendar=_calendar(*symbols))


def test_engine_constraints_cap_symbol_weight() -> None:
    # 50% target with a 20% cap and no other limit buys 20% of equity.
    result = _run_with_constraints(
        symbols_weights=[("AAA", "10", "0.5")],
        constraints_kwargs={
            "max_symbol_weight": Decimal("0.20"),
            "max_industry_weight": Decimal("1"),
            "cash_reserve_rate": Decimal("0"),
        },
    )
    trades = [trade for trade in result.trades if trade.symbol == "AAA"]
    assert sum(trade.quantity for trade in trades) == 2000


def test_engine_constraints_enforce_industry_budget() -> None:
    # Two same-industry names at 30% each with a 40% industry cap get scaled
    # down to 20% each (proportional), regardless of the 100% symbol cap.
    result = _run_with_constraints(
        symbols_weights=[("AAA", "10", "0.3"), ("BBB", "20", "0.3")],
        constraints_kwargs={
            "max_symbol_weight": Decimal("1"),
            "max_industry_weight": Decimal("0.40"),
            "cash_reserve_rate": Decimal("0"),
        },
    )
    quantities = {trade.symbol: trade.quantity for trade in result.trades}
    # 20% of 100k at price 10 -> 2000 shares; at price 20 -> 1000 shares.
    assert quantities == {"AAA": 2000, "BBB": 1000}


def test_engine_constraints_reserve_cash_across_targets() -> None:
    # 100% requested weight with a 10% cash reserve only invests 90%.
    result = _run_with_constraints(
        symbols_weights=[("AAA", "10", "1")],
        constraints_kwargs={
            "max_symbol_weight": Decimal("1"),
            "max_industry_weight": Decimal("1"),
            "cash_reserve_rate": Decimal("0.10"),
        },
    )
    assert sum(trade.quantity for trade in result.trades) == 9000


def test_engine_without_constraints_keeps_legacy_behaviour() -> None:
    # Constraint-free engine path is untouched when constraints are None:
    # a 50% target buys 50% of equity exactly as before.
    engine = DailyEventBacktestEngine(initial_cash=Decimal("100000"))
    assert engine.portfolio_constraints is None
    result = engine.run(
        bars=[_bar(day, "AAA", "10", "10") for day in _calendar("AAA")["AAA"]],
        signals=[TargetWeightSignal(date(2026, 7, 1), "AAA", Decimal("0.5"))],
        execution_calendar=_calendar("AAA"),
    )
    assert sum(trade.quantity for trade in result.trades) == 5000
