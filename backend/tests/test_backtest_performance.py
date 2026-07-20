from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.backtest import BacktestPerformanceAnalyzer


def _equity(trade_date: date, total_equity: str) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=trade_date,
        total_equity=Decimal(total_equity),
    )


def _trade(
    symbol: str,
    side: str,
    quantity: int,
    fill_price: str,
    commission: str = "0",
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        side=side,
        quantity=quantity,
        fill_price=Decimal(fill_price),
        commission=Decimal(commission),
    )


def test_performance_analyzer_calculates_metrics_and_exposures() -> None:
    report = BacktestPerformanceAnalyzer().analyze(
        run=SimpleNamespace(initial_cash=Decimal("100")),
        equity_rows=[
            _equity(date(2026, 7, 1), "100"),
            _equity(date(2026, 7, 2), "110"),
            _equity(date(2026, 7, 3), "99"),
            _equity(date(2026, 7, 4), "120"),
        ],
        trade_rows=[_trade("AAA", "buy", 10, "10")],
        position_rows=[
            SimpleNamespace(
                trade_date=date(2026, 7, 4),
                symbol="AAA",
                market_value=Decimal("120"),
            )
        ],
        industries={"AAA": "Technology"},
        style_values={"momentum": {"AAA": Decimal("0.5")}},
    )

    assert report.total_return == Decimal("0.2")
    assert report.maximum_drawdown == Decimal("-0.1")
    assert report.daily_win_rate == Decimal(2) / Decimal(3)
    assert report.profit_loss_ratio is not None
    assert report.turnover_rate == Decimal("100") / Decimal("107.25")
    assert report.symbol_contributions == {"AAA": Decimal("0.2")}
    assert report.industry_exposure == {"Technology": Decimal("1")}
    assert report.style_exposure == {"momentum": Decimal("0.5")}


def test_performance_analyzer_does_not_reuse_stale_position_after_exit() -> None:
    report = BacktestPerformanceAnalyzer().analyze(
        run=SimpleNamespace(initial_cash=Decimal("100")),
        equity_rows=[
            _equity(date(2026, 7, 1), "100"),
            _equity(date(2026, 7, 2), "110"),
            _equity(date(2026, 7, 3), "120"),
        ],
        trade_rows=[
            _trade("AAA", "buy", 10, "10"),
            _trade("AAA", "sell", 10, "12"),
        ],
        position_rows=[
            SimpleNamespace(
                trade_date=date(2026, 7, 2),
                symbol="AAA",
                market_value=Decimal("110"),
            )
        ],
        industries={"AAA": "Technology"},
    )

    assert report.symbol_contributions == {"AAA": Decimal("0.2")}
    assert report.industry_exposure == {}
