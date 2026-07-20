from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.strategies import (
    EtfRotationStrategy,
    FactorScoreSpec,
    IndexTimingStrategy,
    MultiFactorStrategy,
    StockPoolStrategy,
)


def _row(trade_date: date, symbol: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=trade_date,
        symbol=symbol,
        value=Decimal(value),
    )


def test_stock_pool_selects_top_factor_and_emits_exits() -> None:
    spec = FactorScoreSpec("momentum_5", "v1-none")
    rows = [
        _row(date(2026, 7, 1), "A", "1"),
        _row(date(2026, 7, 1), "B", "2"),
        _row(date(2026, 7, 2), "A", "3"),
        _row(date(2026, 7, 2), "B", "1"),
    ]

    signals = StockPoolStrategy(top_n=1).generate(
        factor_rows={spec: rows},
        security_types={"A": "stock", "B": "stock"},
    )

    assert [(signal.signal_date, signal.symbol, signal.target_weight) for signal in signals] == [
        (date(2026, 7, 1), "B", Decimal("1")),
        (date(2026, 7, 2), "B", Decimal("0")),
        (date(2026, 7, 2), "A", Decimal("1")),
    ]


def test_multi_factor_requires_complete_factor_coverage() -> None:
    momentum = FactorScoreSpec("momentum", "v1", weight=Decimal("0.6"))
    quality = FactorScoreSpec("quality", "v1", weight=Decimal("0.4"))
    strategy = MultiFactorStrategy(top_n=2, allowed_security_types={"stock"})

    signals = strategy.generate(
        factor_rows={
            momentum: [
                _row(date(2026, 7, 1), "A", "1"),
                _row(date(2026, 7, 1), "B", "2"),
            ],
            quality: [_row(date(2026, 7, 1), "A", "3")],
        },
        security_types={"A": "stock", "B": "stock"},
    )

    assert len(signals) == 1
    assert signals[0].symbol == "A"
    assert signals[0].target_weight == Decimal("1")


def test_etf_rotation_filters_non_etf_symbols() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    signals = EtfRotationStrategy().generate(
        factor_rows={
            spec: [
                _row(date(2026, 7, 1), "ETF", "1"),
                _row(date(2026, 7, 1), "STOCK", "2"),
            ]
        },
        security_types={"ETF": "etf", "STOCK": "stock"},
    )

    assert [signal.symbol for signal in signals] == ["ETF"]


def test_index_timing_switches_target_weight_at_threshold() -> None:
    signals = IndexTimingStrategy(threshold=Decimal("0")).generate(
        rows=[
            _row(date(2026, 7, 1), "INDEX", "-0.1"),
            _row(date(2026, 7, 2), "INDEX", "0.1"),
        ],
        symbol="INDEX",
    )

    assert [signal.target_weight for signal in signals] == [Decimal("0"), Decimal("1")]
