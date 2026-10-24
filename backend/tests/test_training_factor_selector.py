"""Unit tests for the per-window training factor selector (training_ic_v1)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.factors import rank_ic
from app.services.training_factor_selector import (
    TrainingSelectionPolicy,
    build_training_selection_policy,
    select_training_factors,
)
from app.strategies import FactorScoreSpec

OPEN_DATES = [date(2026, 1, d) for d in (2, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16)]
SYMBOLS = ["a", "b", "c", "d"]


def _closes() -> dict[str, dict[date, Decimal]]:
    """Constant per-day growth keeps each symbol's forward-return rank fixed.

    A stays flat, B grows 1%/day, D grows 2%/day, C grows 3%/day, so the
    forward-return ordering A < B < D < C holds on every training date.
    """
    closes: dict[str, dict[date, Decimal]] = {}
    for index, trade_date in enumerate(OPEN_DATES):
        closes.setdefault("A", {})[trade_date] = Decimal(100)
        closes.setdefault("B", {})[trade_date] = (Decimal("1.01") ** index) * 100
        closes.setdefault("D", {})[trade_date] = (Decimal("1.02") ** index) * 100
        closes.setdefault("C", {})[trade_date] = (Decimal("1.03") ** index) * 100
    return closes


def _factor_rows(
    factor_name: str,
    values_by_date_index: dict[int, dict[str, int]],
) -> list[SimpleNamespace]:
    rows = []
    for index, trade_date in enumerate(OPEN_DATES):
        for symbol, value in values_by_date_index[index].items():
            rows.append(
                SimpleNamespace(
                    trade_date=trade_date,
                    symbol=symbol.upper(),
                    value=Decimal(value),
                )
            )
    return rows


def _aligned_rows(factor_name: str) -> list[SimpleNamespace]:
    """Daily RankIC alternates between 1.0 (even dates) and 0.8 (odd dates)."""
    values: dict[int, dict[str, int]] = {}
    for index in range(len(OPEN_DATES)):
        if index % 2 == 0:
            values[index] = {"a": 1, "b": 2, "d": 3, "c": 4}
        else:
            values[index] = {"a": 1, "b": 2, "c": 3, "d": 4}
    return _factor_rows(factor_name, values)


def _inverted_rows(factor_name: str) -> list[SimpleNamespace]:
    """Mirror image of ``_aligned_rows``; daily RankIC alternates -1.0/-0.8."""
    values: dict[int, dict[str, int]] = {}
    for index in range(len(OPEN_DATES)):
        if index % 2 == 0:
            values[index] = {"a": 4, "b": 3, "d": 2, "c": 1}
        else:
            values[index] = {"a": 4, "b": 3, "c": 2, "d": 1}
    return _factor_rows(factor_name, values)


def _policy(**overrides: object) -> TrainingSelectionPolicy:
    defaults: dict[str, object] = {
        "min_abs_mean_rank_ic": Decimal("0.02"),
        "min_abs_ic_ir": Decimal("0.30"),
        "min_positive_rate": Decimal("0.55"),
        "min_ic_observations": 5,
        "min_surviving_factors": 1,
    }
    defaults.update(overrides)
    return TrainingSelectionPolicy(**defaults)  # type: ignore[arg-type]


def _select(spec: FactorScoreSpec, rows: list[SimpleNamespace], policy=None):
    return select_training_factors(
        policy=policy or _policy(),
        candidates=[spec],
        rows_by_spec={spec: rows},
        qfq_closes=_closes(),
        training_open_dates=OPEN_DATES,
        training_start_date=OPEN_DATES[0],
        training_end_date=OPEN_DATES[-1],
    )


def test_rank_ic_matches_perfect_and_inverse_orderings() -> None:
    factor = {"AAA": Decimal(1), "BBB": Decimal(2), "CCC": Decimal(3)}
    forward = {"AAA": Decimal("0.01"), "BBB": Decimal("0.02"), "CCC": Decimal("0.03")}
    assert rank_ic.rank_information_coefficient(factor, forward) == Decimal(1)
    inverse = {"AAA": Decimal("0.03"), "BBB": Decimal("0.02"), "CCC": Decimal("0.01")}
    assert rank_ic.rank_information_coefficient(factor, inverse) == Decimal(-1)


def test_selector_maintains_prior_aligned_factor() -> None:
    spec = FactorScoreSpec("alpha_1", "v1-qfq", higher_is_better=True)
    result = _select(spec, _aligned_rows("alpha_1"))
    assert len(result.selected_specs) == 1
    assert result.selected_specs[0].higher_is_better is True
    assert result.selected_specs[0].weight == Decimal("1")
    stat = result.stats[0]
    assert stat.decision == "maintained"
    # The final training date has no in-window forward close, so one date
    # never contributes an observation.
    assert stat.observations == len(OPEN_DATES) - 1
    assert abs(stat.mean_rank_ic - Decimal("0.9")) < Decimal("0.000000001")
    assert abs(stat.ic_ir - Decimal("9")) < Decimal("0.001")
    assert stat.positive_rate == Decimal("1")


def test_selector_maintains_negative_direction_factor() -> None:
    spec = FactorScoreSpec("alpha_1", "v1-qfq", higher_is_better=False)
    result = _select(spec, _inverted_rows("alpha_1"))
    assert result.selected_specs[0].higher_is_better is False
    assert result.stats[0].decision == "maintained"
    assert abs(result.stats[0].mean_rank_ic + Decimal("0.9")) < Decimal("0.000000001")


def test_selector_rejects_prior_conflict_on_short_window() -> None:
    conflicting = FactorScoreSpec("alpha_1", "v1-qfq", higher_is_better=True)
    aligned = FactorScoreSpec("alpha_2", "v1-qfq", higher_is_better=True)
    result = select_training_factors(
        policy=_policy(min_surviving_factors=1),
        candidates=[conflicting, aligned],
        rows_by_spec={
            conflicting: _inverted_rows("alpha_1"),
            aligned: _aligned_rows("alpha_2"),
        },
        qfq_closes=_closes(),
        training_open_dates=OPEN_DATES,
        training_start_date=OPEN_DATES[0],
        training_end_date=OPEN_DATES[-1],
    )
    assert [spec.factor_name for spec in result.selected_specs] == ["alpha_2"]
    rejected = next(stat for stat in result.stats if stat.factor_name == "alpha_1")
    assert rejected.decision == "rejected"
    assert rejected.reason == ("direction_conflicts_prior_without_sufficient_training_days")


def test_selector_flips_direction_only_with_long_enough_window() -> None:
    spec = FactorScoreSpec("alpha_1", "v1-qfq", higher_is_better=True)
    policy = _policy(allow_flip_min_training_days=10)
    result = _select(spec, _inverted_rows("alpha_1"), policy=policy)
    assert len(result.selected_specs) == 1
    assert result.selected_specs[0].higher_is_better is False
    assert result.stats[0].decision == "flipped"


def test_selector_fail_closed_when_survivors_below_minimum() -> None:
    weak = FactorScoreSpec("weak_1", "v1-qfq", higher_is_better=True)
    flat_rows = _factor_rows(
        "weak_1", {index: {"A": 1, "B": 1, "C": 1} for index in range(len(OPEN_DATES))}
    )
    with pytest.raises(ValueError, match="fewer than 2 surviving factors"):
        _select(
            weak,
            flat_rows,
            policy=_policy(min_surviving_factors=2),
        )


def test_selector_drops_candidate_without_rows_but_keeps_run() -> None:
    present = FactorScoreSpec("alpha_1", "v1-qfq", higher_is_better=True)
    missing = FactorScoreSpec("ghost_1", "v1-qfq", higher_is_better=True)
    rows = _aligned_rows("alpha_1")
    result = select_training_factors(
        policy=_policy(min_surviving_factors=1),
        candidates=[missing, present],
        rows_by_spec={present: rows},
        qfq_closes=_closes(),
        training_open_dates=OPEN_DATES,
        training_start_date=OPEN_DATES[0],
        training_end_date=OPEN_DATES[-1],
    )
    assert [spec.factor_name for spec in result.selected_specs] == ["alpha_1"]
    assert result.dropped_candidates == [
        {
            "factor_name": "ghost_1",
            "factor_version": "v1-qfq",
            "reason": "no_factor_values_in_training_window",
        }
    ]


def test_selector_excludes_symbol_without_forward_close() -> None:
    # zzz carries the highest factor value but has a close on the first date
    # only, so it can never form a forward return and must not distort IC.
    spec = FactorScoreSpec("alpha_1", "v1-qfq", higher_is_better=True)
    rows = _aligned_rows("alpha_1")
    rows += _factor_rows("alpha_1", {index: {"z": 9} for index in range(len(OPEN_DATES))})
    closes = _closes()
    closes["Z"] = {OPEN_DATES[0]: Decimal(10)}
    result = select_training_factors(
        policy=_policy(),
        candidates=[spec],
        rows_by_spec={spec: rows},
        qfq_closes=closes,
        training_open_dates=OPEN_DATES,
        training_start_date=OPEN_DATES[0],
        training_end_date=OPEN_DATES[-1],
    )
    stat = result.stats[0]
    assert stat.decision == "maintained"
    assert abs(stat.mean_rank_ic - Decimal("0.9")) < Decimal("0.000000001")


def test_gate_boundaries_use_inclusive_thresholds() -> None:
    spec = FactorScoreSpec("alpha_1", "v1-qfq", higher_is_better=True)
    rows = _aligned_rows("alpha_1")
    at_threshold = _select(spec, rows, policy=_policy(min_abs_mean_rank_ic=Decimal("0.9")))
    assert at_threshold.stats[0].decision == "maintained"
    with pytest.raises(ValueError, match="mean_rank_ic_below_threshold\\(0.9000000000\\)"):
        _select(spec, rows, policy=_policy(min_abs_mean_rank_ic=Decimal("0.95")))


def test_config_hash_ignores_weights_and_binds_thresholds() -> None:
    base = [
        FactorScoreSpec("alpha_1", "v1-qfq"),
        FactorScoreSpec("alpha_2", "v1-qfq", higher_is_better=False),
    ]
    reweighted = [
        FactorScoreSpec("alpha_1", "v1-qfq", weight=Decimal("3")),
        FactorScoreSpec("alpha_2", "v1-qfq", weight=Decimal("7"), higher_is_better=False),
    ]
    policy = _policy()
    assert policy.config_hash(base) == policy.config_hash(reweighted)
    tighter = _policy(min_abs_ic_ir=Decimal("0.35"))
    assert policy.config_hash(base) != tighter.config_hash(base)
    assert len(policy.config_hash(base)) == 8


def test_build_training_selection_policy_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown selection_policy"):
        build_training_selection_policy("magic_v9")
    assert build_training_selection_policy("training_ic_v1").strategy_id == ("training_ic_v1")


def test_policy_rejects_non_v1_knobs() -> None:
    with pytest.raises(ValueError, match="ic_horizon=1 only"):
        TrainingSelectionPolicy(ic_horizon=5)
    with pytest.raises(ValueError, match="equal weighting only"):
        TrainingSelectionPolicy(weighting="ic_ir")  # type: ignore[arg-type]
