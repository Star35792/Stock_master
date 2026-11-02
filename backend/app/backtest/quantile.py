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
- Buckets never split equal factor values by symbol. With cross-sectional
  dispersion they are mutually exclusive and together cover the ranked
  universe; a zero-dispersion date is withheld as empty layers.
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

    Returns one :class:`QuantilePartition` per decision date. Nominal equal-size
    boundaries assign each complete equal-value group to one layer, so symbol
    names never decide a layer. The lowest and highest distinct groups always
    anchor the endpoint layers; intermediate layers may be empty when the factor
    has low cardinality. A date with no cross-sectional dispersion yields empty
    layers because no defensible top-minus-bottom comparison exists.

    ``max_assets_per_layer`` is a nominal cap: the boundary member's complete
    equal-value group is retained, so the actual count may exceed the cap.
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
                QuantilePartition(
                    decision_date=decision_date,
                    layers=tuple(tuple() for _ in range(n_layers)),
                )
            )
            continue
        # Symbol is only a deterministic display order within an equal-value
        # group; it must never determine which layer receives that group.
        ordered = sorted(values.items(), key=lambda kv: (kv[1], kv[0]))
        total = len(ordered)
        groups = _equal_value_groups(ordered)
        if len(groups) < 2:
            partitions.append(
                QuantilePartition(
                    decision_date=decision_date,
                    layers=tuple(tuple() for _ in range(n_layers)),
                )
            )
            continue
        base = total // n_layers
        remainder = total % n_layers
        sizes = [
            base + (1 if index < remainder else 0) for index in range(n_layers)
        ]
        boundaries: list[int] = []
        cumulative = 0
        for size in sizes:
            cumulative += size
            boundaries.append(cumulative)

        layer_members: list[list[tuple[str, Decimal]]] = [
            [] for _ in range(n_layers)
        ]
        cursor = 0
        last_group_index = len(groups) - 1
        for group_index, group in enumerate(groups):
            group_end = cursor + len(group)
            if group_index == 0:
                layer_index = 0
            elif group_index == last_group_index:
                layer_index = n_layers - 1
            else:
                midpoint_twice = cursor + group_end - 1
                layer_index = next(
                    (
                        index
                        for index, boundary in enumerate(boundaries)
                        if midpoint_twice < boundary * 2
                    ),
                    n_layers - 1,
                )
            layer_members[layer_index].extend(group)
            cursor = group_end

        layers: list[tuple[str, ...]] = []
        for members in layer_members:
            if max_assets_per_layer is not None:
                members = _truncate_without_splitting_ties(
                    members, max_assets_per_layer
                )
            layers.append(tuple(symbol for symbol, _ in members))
        partitions.append(
            QuantilePartition(decision_date=decision_date, layers=tuple(layers))
        )
    return partitions


def _equal_value_groups(
    ordered: list[tuple[str, Decimal]],
) -> list[list[tuple[str, Decimal]]]:
    groups: list[list[tuple[str, Decimal]]] = []
    for item in ordered:
        if not groups or groups[-1][0][1] != item[1]:
            groups.append([item])
        else:
            groups[-1].append(item)
    return groups


def _truncate_without_splitting_ties(
    members: list[tuple[str, Decimal]], nominal_limit: int
) -> list[tuple[str, Decimal]]:
    if len(members) <= nominal_limit:
        return members
    boundary_value = members[nominal_limit - 1][1]
    return [item for item in members if item[1] <= boundary_value]
