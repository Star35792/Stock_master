from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.strategies import (
    EtfRotationStrategy,
    FactorScoreSpec,
    FutureStrategyDataError,
    IndexTimingStrategy,
    MultiFactorStrategy,
    StockPoolStrategy,
    StrategyFactorFrame,
    StrategyInputError,
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

    signals = StockPoolStrategy(top_n=1).generate_history(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 2), rows_by_factor={spec: rows}
        ),
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
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                momentum: [
                    _row(date(2026, 7, 1), "A", "1"),
                    _row(date(2026, 7, 1), "B", "2"),
                ],
                quality: [_row(date(2026, 7, 1), "A", "3")],
            },
        ),
        security_types={"A": "stock", "B": "stock"},
    )

    assert len(signals) == 1
    assert signals[0].symbol == "A"
    assert signals[0].target_weight == Decimal("1")


def test_cross_section_selection_is_deterministic_for_equal_scores() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(top_n=1)
    rows = [
        _row(date(2026, 7, 1), "BBB", "1"),
        _row(date(2026, 7, 1), "AAA", "1"),
    ]

    first = strategy.generate(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1), rows_by_factor={spec: rows}
        ),
        security_types={"AAA": "stock", "BBB": "stock"},
    )
    second = strategy.generate(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1), rows_by_factor={spec: reversed(rows)}
        ),
        security_types={"AAA": "stock", "BBB": "stock"},
    )

    assert first == second


def test_etf_rotation_filters_non_etf_symbols() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    signals = EtfRotationStrategy().generate(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                spec: [
                    _row(date(2026, 7, 1), "ETF", "1"),
                    _row(date(2026, 7, 1), "STOCK", "2"),
                ]
            },
        ),
        security_types={"ETF": "etf", "STOCK": "stock"},
    )

    assert [signal.symbol for signal in signals] == ["ETF"]


def test_index_timing_switches_target_weight_at_threshold() -> None:
    spec = FactorScoreSpec("timing", "v1")
    signals = IndexTimingStrategy(threshold=Decimal("0")).generate_history(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 2),
            rows_by_factor={
                spec: [
                    _row(date(2026, 7, 1), "INDEX", "-0.1"),
                    _row(date(2026, 7, 2), "INDEX", "0.1"),
                ]
            },
        ),
        symbol="INDEX",
    )

    assert [signal.target_weight for signal in signals] == [Decimal("0"), Decimal("1")]


def test_strategy_factor_frame_rejects_future_and_duplicate_observations() -> None:
    spec = FactorScoreSpec("momentum", "v1")

    with pytest.raises(FutureStrategyDataError, match="future data"):
        StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={spec: [_row(date(2026, 7, 2), "AAA", "1")]},
        )

    duplicate = _row(date(2026, 7, 1), "AAA", "1")
    with pytest.raises(StrategyInputError, match="duplicate"):
        StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={spec: [duplicate, duplicate]},
        )


def test_strategy_decision_rejects_a_multi_date_frame() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    frame = StrategyFactorFrame.from_rows(
        as_of_date=date(2026, 7, 2),
        rows_by_factor={
            spec: [
                _row(date(2026, 7, 1), "AAA", "1"),
                _row(date(2026, 7, 2), "AAA", "2"),
            ]
        },
    )

    with pytest.raises(StrategyInputError, match="exact as_of_date"):
        StockPoolStrategy(top_n=1).generate(
            frame=frame,
            security_types={"AAA": "stock"},
        )


def test_future_observations_do_not_change_an_earlier_decision() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(top_n=1)
    first_day = [
        _row(date(2026, 7, 1), "AAA", "1"),
        _row(date(2026, 7, 1), "BBB", "2"),
    ]
    baseline = StrategyFactorFrame.from_rows(
        as_of_date=date(2026, 7, 1), rows_by_factor={spec: first_day}
    )
    with_future = StrategyFactorFrame.from_rows(
        as_of_date=date(2026, 7, 2),
        rows_by_factor={
            spec: [
                *first_day,
                _row(date(2026, 7, 2), "AAA", "100"),
                _row(date(2026, 7, 2), "BBB", "-100"),
            ]
        },
    )

    baseline_signal = strategy.generate_history(
        frame=baseline,
        security_types={"AAA": "stock", "BBB": "stock"},
    )[0]
    historical_signal = strategy.generate_history(
        frame=with_future,
        security_types={"AAA": "stock", "BBB": "stock"},
    )[0]

    assert historical_signal == baseline_signal


def test_strategy_factor_frame_snapshot_is_order_independent() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    rows = [
        _row(date(2026, 7, 1), "BBB", "2"),
        _row(date(2026, 7, 1), "AAA", "1"),
    ]

    first = StrategyFactorFrame.from_rows(
        as_of_date=date(2026, 7, 1), rows_by_factor={spec: rows}
    ).snapshot()
    second = StrategyFactorFrame.from_rows(
        as_of_date=date(2026, 7, 1), rows_by_factor={spec: reversed(rows)}
    ).snapshot()

    assert first == second
    assert first["row_count"] == 2


def test_strategy_factor_frame_copies_mutable_source_rows() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    source = _row(date(2026, 7, 1), "aaa", "1")
    frame = StrategyFactorFrame.from_rows(
        as_of_date=date(2026, 7, 1), rows_by_factor={spec: [source]}
    )
    snapshot = frame.snapshot()

    source.trade_date = date(2026, 7, 2)
    source.value = Decimal("99")

    observation = frame.rows_by_factor[spec][0]
    assert observation.symbol == "AAA"
    assert observation.trade_date == date(2026, 7, 1)
    assert observation.value == Decimal("1")
    assert frame.snapshot() == snapshot
