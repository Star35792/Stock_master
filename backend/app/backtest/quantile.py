"""Quantile (factor-layer) portfolio partitioning for backtests.

Pure, framework-agnostic helpers used by the factor-selection backtest to split
the investable universe into ``n`` mutually exclusive buckets by a single
factor's cross-sectional rank on each decision date. The engine then runs one
equal-weight long-only portfolio per bucket; the top-minus-bottom spread is a
*statistical* return difference (A-shares cannot be shorted), never a tradable
short position.

Financial-correctness invariants (enforced by the caller, asserted in tests):
- ``factor_values_by_date`` must hold only decision-date-available factor values
  (no future data); the partition itself is a pure ranking operation.
- Buckets are mutually exclusive and together cover the ranked universe.
- Layer 0 is the LOWEST factor values (bottom), layer ``n-1`` the HIGHEST (top).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class QuantilePartition:
    """One decision date's bucket assignment: ``layers[layer_index]`` is the
    tuple of symbols in that bucket (empty tuple when no data that date)."""

    decision_date: date
    layers: tuple[tuple[str, ...], ...]


def partition_universe_by_factor(
    factor_values_by_date: Mapping[date, Mapping[str, Decimal]],
    decision_dates: list[date],
    n_layers: int,
    *,
    max_assets_per_layer: int | None = None,
) -> list[QuantilePartition]:
    """Split the universe into ``n_layers`` equal-ish buckets per decision date.

    Returns one :class:`QuantilePartition` per decision date. Each partition's
    ``layers`` are mutually exclusive and cover every ranked symbol. When
    ``max_assets_per_layer`` is set, each bucket is truncated to its leading
    (lowest-factor-value) members — documented as a coarse cap, not a selection
    refinement.
    """
    if n_layers < 2:
        raise ValueError("n_layers must be >= 2")
    if max_assets_per_layer is not None and max_assets_per_layer < 1:
        raise ValueError("max_assets_per_layer must be >= 1")

    partitions: list[QuantilePartition] = []
    for decision_date in decision_dates:
        values = factor_values_by_date.get(decision_date)
        if not values:
            partitions.append(
                QuantilePartition(decision_date=decision_date, layers=tuple())
            )
            continue
        # Ascending by factor value, symbol tiebreak -> stable, deterministic.
        ordered = sorted(values.items(), key=lambda kv: (kv[1], kv[0]))
        total = len(ordered)
        base = total // n_layers
        remainder = total % n_layers
        sizes = [
            base + (1 if index < remainder else 0) for index in range(n_layers)
        ]
        layers: list[tuple[str, ...]] = []
        cursor = 0
        for layer_index in range(n_layers):
            size = sizes[layer_index]
            bucket = ordered[cursor : cursor + size]
            cursor += size
            if not bucket:
                layers.append(tuple())
                continue
            symbols = [symbol for symbol, _ in bucket]
            if max_assets_per_layer is not None:
                symbols = symbols[:max_assets_per_layer]
            layers.append(tuple(symbols))
        partitions.append(
            QuantilePartition(decision_date=decision_date, layers=tuple(layers))
        )
    return partitions
