from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.strategies import (
    EtfRotationStrategy,
    FactorScoreSpec,
    FutureStrategyDataError,
    IndexTimingStrategy,
    MinimumScorePolicy,
    MultiFactorStrategy,
    StockPoolStrategy,
    StrategyFactorFrame,
    StrategyInputError,
    WeightingMode,
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


def test_coverage_ratio_admits_partially_covered_symbols() -> None:
    momentum = FactorScoreSpec("momentum", "v1", weight=Decimal("0.6"))
    quality = FactorScoreSpec("quality", "v1", weight=Decimal("0.4"))
    strategy = MultiFactorStrategy(
        top_n=2,
        allowed_security_types={"stock"},
        coverage_ratio=Decimal("0.5"),
    )

    decision = strategy.decide(
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

    assert decision.selected_symbols == 2
    assert {signal.symbol for signal in decision.signals if signal.target_weight > 0} == {
        "A",
        "B",
    }
    assert decision.coverage_threshold == Decimal("0.5")


def test_full_coverage_is_still_the_default() -> None:
    momentum = FactorScoreSpec("momentum", "v1")
    quality = FactorScoreSpec("quality", "v1")
    strategy = MultiFactorStrategy(top_n=2, allowed_security_types={"stock"})

    decision = strategy.decide(
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

    assert decision.selected_symbols == 1
    assert decision.dropped_for_coverage == 1


def test_decision_reports_how_many_symbols_actually_participated() -> None:
    momentum = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(top_n=5)

    decision = strategy.decide(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                momentum: [
                    _row(date(2026, 7, 1), "A", "1"),
                    _row(date(2026, 7, 1), "B", "2"),
                    _row(date(2026, 7, 1), "C", "3"),
                ],
            },
        ),
        security_types={"A": "stock", "B": "stock", "C": "stock"},
    )

    assert decision.eligible_symbols == 3
    assert decision.scored_symbols == 3
    assert decision.selected_symbols == 3
    assert decision.invested_weight == sum(
        (signal.target_weight for signal in decision.signals if signal.target_weight > 0),
        Decimal(),
    )


def test_hold_cash_policy_leaves_unallocated_weight_as_cash() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(
        top_n=2,
        weighting_mode=WeightingMode.EQUAL_WEIGHT_PARTIAL,
        minimum_score_policy=MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE,
        invested_weight=Decimal("0.60"),
    )

    decision = strategy.decide(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                spec: [
                    _row(date(2026, 7, 1), "A", "1"),
                    _row(date(2026, 7, 1), "B", "2"),
                ],
            },
        ),
        security_types={"A": "stock", "B": "stock"},
    )

    assert decision.invested_weight == Decimal("0.60")
    assert sum(
        (signal.target_weight for signal in decision.signals if signal.target_weight > 0),
        Decimal(),
    ) == Decimal("0.60")


def test_score_weighted_mode_concentrates_weight_on_the_strongest_name() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(
        top_n=2,
        weighting_mode=WeightingMode.SCORE_WEIGHTED,
        minimum_score_policy=MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE,
        invested_weight=Decimal("0.80"),
    )

    decision = strategy.decide(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                spec: [
                    _row(date(2026, 7, 1), "A", "1"),
                    _row(date(2026, 7, 1), "B", "2"),
                ],
            },
        ),
        security_types={"A": "stock", "B": "stock"},
    )

    weights = {signal.symbol: signal.target_weight for signal in decision.signals}
    assert weights["B"] > weights["A"]
    assert decision.invested_weight == Decimal("0.80")


def test_no_covered_symbol_produces_a_full_exit_and_holds_cash() -> None:
    momentum = FactorScoreSpec("momentum", "v1")
    quality = FactorScoreSpec("quality", "v1")
    strategy = MultiFactorStrategy(top_n=2, allowed_security_types={"stock"})

    decision = strategy.decide(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 2),
            rows_by_factor={
                momentum: [_row(date(2026, 7, 2), "A", "1")],
                quality: [],
            },
        ),
        security_types={"A": "stock"},
        previous_symbols={"A", "B"},
    )

    exits = {
        signal.symbol: signal.target_weight
        for signal in decision.signals
        if signal.symbol in {"A", "B"}
    }
    assert exits == {"A": Decimal("0"), "B": Decimal("0")}
    assert decision.invested_weight == Decimal("0")
    assert decision.scored_symbols == 0
    assert decision.dropped_for_coverage == 1


def test_hold_cash_with_full_equal_weight_cannot_leave_cash() -> None:
    with pytest.raises(ValueError, match="always invests 100%"):
        StockPoolStrategy(
            top_n=1,
            weighting_mode=WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED,
            minimum_score_policy=MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE,
            invested_weight=Decimal("0.5"),
        )


def test_score_weighted_mode_rejects_a_non_positive_summed_score() -> None:
    quality = FactorScoreSpec("quality", "v1", weight=Decimal("-1"))
    strategy = StockPoolStrategy(
        top_n=2,
        weighting_mode=WeightingMode.SCORE_WEIGHTED,
        minimum_score_policy=MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE,
        invested_weight=Decimal("0.8"),
    )

    with pytest.raises(ValueError, match="negative-score symbol"):
        strategy.decide(
            frame=StrategyFactorFrame.from_rows(
                as_of_date=date(2026, 7, 1),
                rows_by_factor={
                    quality: [
                        _row(date(2026, 7, 1), "AAA", "5"),
                        _row(date(2026, 7, 1), "BBB", "5"),
                    ],
                },
            ),
            security_types={"AAA": "stock", "BBB": "stock"},
        )


def test_coverage_ratio_must_be_within_the_unit_interval() -> None:
    with pytest.raises(ValueError, match="coverage_ratio"):
        StockPoolStrategy(top_n=1, coverage_ratio=Decimal("1.5"))


def test_score_weighted_rejects_a_negative_score_symbol_instead_of_shorting() -> None:
    """A negative normalized score must fail closed: shorting is not executable."""

    pos = FactorScoreSpec("pos", "v1", weight=Decimal("2"))
    neg = FactorScoreSpec("neg", "v1", weight=Decimal("-1"))
    strategy = StockPoolStrategy(
        top_n=2,
        weighting_mode=WeightingMode.SCORE_WEIGHTED,
        minimum_score_policy=MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE,
        invested_weight=Decimal("0.8"),
    )

    with pytest.raises(ValueError, match="negative-score symbol"):
        strategy.decide(
            frame=StrategyFactorFrame.from_rows(
                as_of_date=date(2026, 7, 1),
                rows_by_factor={
                    pos: [
                        _row(date(2026, 7, 1), "AAA", "1"),
                        _row(date(2026, 7, 1), "BBB", "2"),
                    ],
                    neg: [
                        _row(date(2026, 7, 1), "AAA", "4"),
                        _row(date(2026, 7, 1), "BBB", "3"),
                    ],
                },
            ),
            security_types={"AAA": "stock", "BBB": "stock"},
        )


def test_history_counters_sum_per_date_while_selection_is_a_union() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(top_n=2)

    decision = strategy.decide_history(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 2),
            rows_by_factor={
                spec: [
                    _row(date(2026, 7, 1), "A", "1"),
                    _row(date(2026, 7, 1), "B", "2"),
                    _row(date(2026, 7, 1), "C", "3"),
                    _row(date(2026, 7, 2), "A", "3"),
                    _row(date(2026, 7, 2), "B", "2"),
                    _row(date(2026, 7, 2), "C", "1"),
                ],
            },
        ),
        security_types={"A": "stock", "B": "stock", "C": "stock"},
    )

    assert decision.eligible_symbols == 6
    assert decision.selected_symbols == 4
    assert decision.distinct_selected_symbols == 3
    assert decision.invested_weight == Decimal("1")


def test_equal_weight_residual_is_collected_exactly() -> None:
    """1/3 per name must sum to exactly the invested budget."""
    spec = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(
        top_n=3,
        weighting_mode=WeightingMode.EQUAL_WEIGHT_PARTIAL,
        invested_weight=Decimal("0.60"),
    )

    decision = strategy.decide(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                spec: [
                    _row(date(2026, 7, 1), "A", "1"),
                    _row(date(2026, 7, 1), "B", "2"),
                    _row(date(2026, 7, 1), "C", "3"),
                ],
            },
        ),
        security_types={"A": "stock", "B": "stock", "C": "stock"},
    )

    assert decision.invested_weight == Decimal("0.60")
    assert sum(
        (signal.target_weight for signal in decision.signals if signal.target_weight > 0),
        Decimal(),
    ) == Decimal("0.60")


def test_minimum_score_filters_before_top_n_and_leaves_cash() -> None:
    spec = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(
        top_n=2,
        weighting_mode=WeightingMode.EQUAL_WEIGHT_PARTIAL,
        minimum_score_policy=MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE,
        minimum_score=Decimal("0.70"),
        invested_weight=Decimal("0.60"),
    )

    decision = strategy.decide(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                spec: [
                    _row(date(2026, 7, 1), "A", "3"),
                    _row(date(2026, 7, 1), "B", "2"),
                    _row(date(2026, 7, 1), "C", "1"),
                ],
            },
        ),
        security_types={"A": "stock", "B": "stock", "C": "stock"},
    )

    selected = {signal.symbol for signal in decision.signals if signal.target_weight > 0}
    assert selected == {"A"}
    assert decision.selected_symbols == 1
    assert decision.below_minimum_score_count == 2
    assert decision.invested_weight == Decimal("0.60")
    assert sum(
        (signal.target_weight for signal in decision.signals if signal.target_weight > 0),
        Decimal(),
    ) == Decimal("0.60")


def test_generate_emits_signals_in_alphabetical_order_within_a_date() -> None:
    """Signal order within a date must remain alphabetical for backward compat."""
    spec = FactorScoreSpec("momentum", "v1")
    strategy = StockPoolStrategy(top_n=3)

    signals = strategy.generate(
        frame=StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                spec: [
                    _row(date(2026, 7, 1), "Z", "3"),
                    _row(date(2026, 7, 1), "A", "1"),
                    _row(date(2026, 7, 1), "M", "2"),
                ],
            },
        ),
        security_types={"A": "stock", "M": "stock", "Z": "stock"},
    )

    positive = [signal.symbol for signal in signals if signal.target_weight > 0]
    assert positive == ["A", "M", "Z"]


def test_score_weighted_rejects_non_finite_scores() -> None:
    pos = FactorScoreSpec("pos", "v1", weight=Decimal("1"))
    strategy = StockPoolStrategy(
        top_n=2,
        weighting_mode=WeightingMode.SCORE_WEIGHTED,
        minimum_score_policy=MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE,
        invested_weight=Decimal("0.8"),
    )

    with pytest.raises(ValueError, match="must be finite"):
        strategy.decide(
            frame=StrategyFactorFrame.from_rows(
                as_of_date=date(2026, 7, 1),
                rows_by_factor={
                    pos: [
                        SimpleNamespace(
                            trade_date=date(2026, 7, 1), symbol="AAA", value=Decimal("NaN")
                        ),
                        SimpleNamespace(
                            trade_date=date(2026, 7, 1), symbol="BBB", value=Decimal("1")
                        ),
                    ],
                },
            ),
            security_types={"AAA": "stock", "BBB": "stock"},
        )


def test_factor_value_rows_reject_non_finite_values() -> None:
    spec = FactorScoreSpec("momentum", "v1")

    with pytest.raises(ValueError, match="must be finite"):
        StrategyFactorFrame.from_rows(
            as_of_date=date(2026, 7, 1),
            rows_by_factor={
                spec: [
                    SimpleNamespace(
                        trade_date=date(2026, 7, 1), symbol="AAA", value=Decimal("Inf")
                    ),
                ],
            },
        )
