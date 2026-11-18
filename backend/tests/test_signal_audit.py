from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.backtest import TargetWeightSignal
from app.strategies.signal_audit import (
    MAX_DATES,
    MAX_ROWS,
    MAX_SIGNALS,
    AuditObservation,
    audit_signals,
    canonical_signals,
)

D = Decimal
DAY = date(2026, 1, 5)
ROWS = tuple(AuditObservation(DAY + timedelta(days=i), "A", "f", D(i + 1))
             for i in range(5))


def causal(rows):
    return [TargetWeightSignal(row.trade_date, row.symbol, row.value / 10) for row in rows]


def test_causal_callback_passes_and_leaves_inputs_unchanged():
    original = tuple(ROWS)
    report = audit_signals(ROWS, causal, expected_signals=causal(ROWS))
    assert report.status == "passed"
    assert report.evaluations == 9
    assert len(report.cutoffs) == 3
    assert all(check.differences == 0 for check in report.checks)
    assert ROWS == original


@pytest.mark.parametrize("mode", ["future_shift", "full_sample_normalization"])
def test_detects_future_dependent_preprocessing(mode):
    def leaky(rows):
        if mode == "future_shift":
            values = [row.value for row in rows[1:]] + [D(0)]
        else:
            values = [row.value / sum(item.value for item in rows) for row in rows]
        return [TargetWeightSignal(row.trade_date, row.symbol, value)
                for row, value in zip(rows, values, strict=True)]
    report = audit_signals(ROWS, leaky)
    assert report.status == "failed"
    assert any(check.differences for check in report.checks)
    assert sum(len(check.examples) for check in report.checks) <= 12


def test_explicit_zero_exit_is_different_from_no_signal():
    expected = [TargetWeightSignal(DAY, "A", D(0))]
    assert canonical_signals(expected) != canonical_signals([])
    report = audit_signals(ROWS, causal, expected_signals=expected)
    assert report.status == "insufficient"
    assert not report.checks


def test_observable_nondeterminism_is_error():
    calls = 0

    def unstable(rows):
        nonlocal calls
        calls += 1
        return [TargetWeightSignal(rows[0].trade_date, "A", D(calls))]
    assert audit_signals(ROWS, unstable).status == "error"


@pytest.mark.parametrize("weight", [D("NaN"), D("Infinity"), 0.5, None])
def test_invalid_weights_are_never_zero_filled(weight):
    with pytest.raises(ValueError):
        canonical_signals([TargetWeightSignal(DAY, "A", weight)])


def test_duplicate_signal_and_observation_are_errors():
    with pytest.raises(ValueError):
        canonical_signals([TargetWeightSignal(DAY, "A", D(0))] * 2)
    assert audit_signals([ROWS[0]] * 2, causal).status == "error"


@pytest.mark.parametrize("rows", [(), ROWS[:1]])
def test_insufficient_dates_are_not_passes(rows):
    assert audit_signals(rows, causal).status == "insufficient"


def test_no_comparable_signal_is_insufficient():
    assert audit_signals(ROWS, lambda _: []).status == "insufficient"


def test_none_preserved_and_effective_input_changes_reported():
    rows = tuple(replace(row, value=None) for row in ROWS)
    report = audit_signals(rows, lambda data: [
        TargetWeightSignal(row.trade_date, row.symbol, D(1)) for row in data
    ])
    assert report.status == "passed"  # truncation still changes inputs
    assert all(item.changed_input_rows == 0 for item in report.checks if item.kind == "perturb")


@pytest.mark.parametrize("budget", [-1, 0, float("nan"), float("inf")])
def test_invalid_time_budget_fails_closed(budget):
    assert audit_signals(ROWS, causal, time_budget=budget).status == "insufficient"


def test_cooperative_deadline(monkeypatch):
    ticks = iter([0, 0, 11])
    monkeypatch.setattr("app.strategies.signal_audit.monotonic", lambda: next(ticks))
    assert audit_signals(ROWS, causal).status == "insufficient"


def test_bounded_rows_dates_features_and_signals():
    rows = (replace(ROWS[0], symbol=str(i)) for i in range(MAX_ROWS + 1))
    report = audit_signals(rows, causal)
    assert report.status == "insufficient"
    assert "20000" in report.reason
    assert report.evaluations == 0
    dates = [replace(ROWS[0], trade_date=DAY + timedelta(days=i)) for i in range(MAX_DATES + 1)]
    assert audit_signals(dates, causal).status == "insufficient"
    features = [replace(row, feature=str(i)) for i in range(9) for row in ROWS]
    assert audit_signals(features, causal).status == "insufficient"
    with pytest.raises(ValueError, match="数量"):
        canonical_signals(TargetWeightSignal(DAY, str(i), D(1)) for i in range(MAX_SIGNALS + 1))
