"""Pure-function tests for the quantile partition and curve-stitch helpers.

These helpers carry the financial-correctness invariants of the layered backtest
and the rolling combined-curve feature, so they are exercised directly without a
database or engine.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtest.curve_stitch import (
    WindowCostSummary,
    stitch_equity_curves,
    window_cost_summary_from_trades,
)
from app.backtest.quantile import partition_universe_by_factor


def test_partition_splits_into_mutually_exclusive_layers() -> None:
    d = date(2026, 1, 2)
    values = {
        d: {
            "AAA": Decimal("1"),
            "BBB": Decimal("2"),
            "CCC": Decimal("3"),
            "DDD": Decimal("4"),
        }
    }
    partitions = partition_universe_by_factor(values, [d], n_layers=2)
    assert len(partitions) == 1
    layers = partitions[0].layers
    assert len(layers) == 2
    # Layer 0 is the LOWEST factor values (bottom), layer 1 the HIGHEST (top).
    assert set(layers[0]) == {"AAA", "BBB"}
    assert set(layers[1]) == {"CCC", "DDD"}
    # Buckets are mutually exclusive and together cover the universe.
    union = set(layers[0]) | set(layers[1])
    assert union == {"AAA", "BBB", "CCC", "DDD"}
    assert not (set(layers[0]) & set(layers[1]))


def test_partition_handles_odd_bucket_sizes_deterministically() -> None:
    d = date(2026, 1, 2)
    # 5 symbols split into 2 layers -> sizes [3, 2].
    values = {
        d: {
            "AAA": Decimal("1"),
            "BBB": Decimal("2"),
            "CCC": Decimal("3"),
            "DDD": Decimal("4"),
            "EEE": Decimal("5"),
        }
    }
    layers = partition_universe_by_factor(values, [d], n_layers=2)[0].layers
    assert len(layers[0]) == 3
    assert len(layers[1]) == 2
    assert set(layers[0]) == {"AAA", "BBB", "CCC"}
    assert set(layers[1]) == {"DDD", "EEE"}


def test_partition_empty_date_yields_empty_layers() -> None:
    missing = date(2026, 1, 3)
    present = date(2026, 1, 2)
    values = {present: {"AAA": Decimal("1"), "BBB": Decimal("2")}}
    partitions = partition_universe_by_factor(values, [missing], n_layers=2)
    assert partitions[0].layers == (tuple(), tuple())


def test_partition_applies_max_assets_per_layer() -> None:
    d = date(2026, 1, 2)
    values = {
        d: {
            "AAA": Decimal("1"),
            "BBB": Decimal("2"),
            "CCC": Decimal("3"),
            "DDD": Decimal("4"),
        }
    }
    layers = partition_universe_by_factor(
        values, [d], n_layers=2, max_assets_per_layer=1
    )[0].layers
    # Each layer keeps only its leading (lowest-factor-value) member.
    assert layers[0] == ("AAA",)
    assert layers[1] == ("CCC",)


def test_partition_rejects_single_layer() -> None:
    d = date(2026, 1, 2)
    values = {d: {"AAA": Decimal("1"), "BBB": Decimal("2")}}
    with pytest.raises(ValueError, match="n_layers must be >= 2"):
        partition_universe_by_factor(values, [d], n_layers=1)


def test_stitch_continues_principal_across_windows() -> None:
    initial = Decimal("100000")
    window_one = [
        (date(2026, 1, 2), initial),
        (date(2026, 1, 3), initial * Decimal("1.1")),
    ]
    window_two = [
        (date(2026, 1, 4), initial),
        (date(2026, 1, 5), initial * Decimal("1.2")),
    ]
    costs = [
        WindowCostSummary(commission=Decimal("1"), traded_notional=Decimal("1000")),
        WindowCostSummary(commission=Decimal("1"), traded_notional=Decimal("1000")),
    ]
    combined = stitch_equity_curves([window_one, window_two], costs, initial)
    # First window stays on the initial-cash basis.
    assert combined.total_equity[0:2] == (initial, initial * Decimal("1.1"))
    # Second window is scaled so its opening equity continues from the previous
    # window's close (110000); scale factor is 1.1, not a principal reset.
    assert combined.total_equity[2] == initial * Decimal("1.1")
    assert combined.total_equity[3] == initial * Decimal("1.32")
    assert combined.total_return == Decimal("0.32")
    assert combined.window_count == 2


def test_stitch_single_window_rebased_to_initial() -> None:
    initial = Decimal("100000")
    window_one = [
        (date(2026, 1, 2), initial),
        (date(2026, 1, 3), initial * Decimal("1.05")),
    ]
    costs = [WindowCostSummary(commission=Decimal("0"), traded_notional=Decimal("0"))]
    combined = stitch_equity_curves([window_one], costs, initial)
    assert combined.window_count == 1
    assert combined.total_return == Decimal("0.05")


def test_stitch_rejects_overlapping_windows() -> None:
    initial = Decimal("100000")
    window_one = [(date(2026, 1, 2), initial), (date(2026, 1, 4), initial)]
    window_two = [(date(2026, 1, 3), initial), (date(2026, 1, 5), initial)]
    costs = [
        WindowCostSummary(commission=Decimal("0"), traded_notional=Decimal("0")),
        WindowCostSummary(commission=Decimal("0"), traded_notional=Decimal("0")),
    ]
    with pytest.raises(ValueError, match="overlapping"):
        stitch_equity_curves([window_one, window_two], costs, initial)


def test_window_cost_summary_from_trades() -> None:
    trades = [SimpleNamespace(quantity=100, fill_price=Decimal("10"))]
    summary = window_cost_summary_from_trades(trades, commission=Decimal("0.3"))
    assert summary.commission == Decimal("0.3")
    assert summary.traded_notional == Decimal("1000")
