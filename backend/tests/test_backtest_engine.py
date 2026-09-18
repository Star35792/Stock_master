from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtest import DailyEventBacktestEngine, TargetWeightSignal


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
