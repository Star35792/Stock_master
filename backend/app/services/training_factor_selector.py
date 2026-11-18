"""Per-window training factor selection for rolling walk-forward runs.

Implements the ``training_ic_v1`` policy defined in
``aiDoc/plans/20261024-1522-training-window-selection-plan.md``: every
candidate factor is scored with daily RankIC1 computed **only** from
training-window factor values and forward-adjusted closes, then maintained
or rejected against pre-registered thresholds.  A factor whose training
direction conflicts with its prior direction is rejected unless the training
window is long enough to justify a flip; the selector never invents
directions from thin evidence.  If fewer than ``min_surviving_factors``
candidates survive, the whole window fails closed — the caller aborts the
rolling run instead of silently falling back to the full candidate pool.

Known scope limitations (declared, not hidden): forward returns come from the
curated ``market.prices_qfq`` layer with exchange-calendar alignment, which is
a different (stricter) methodology than the LEAD(1) evidence in
``compute_warm_factor_ic.py``; the two are not numerically comparable.  All
thresholds are authored, not estimated, and must not be tuned against
test-window returns.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Literal

from app.factors import rank_ic
from app.services.training_diagnostics import (
    DailyRankICEvidence,
    TrainingDiagnosticsPolicy,
    build_training_diagnostics,
)
from app.strategies import FactorScoreSpec

POLICY_VERSION = "training_ic_v1"

_QUANT = Decimal("0.0000000001")


@dataclass(frozen=True)
class TrainingSelectionPolicy:
    """Pre-registered selection policy; every field enters the config hash."""

    strategy_id: str = POLICY_VERSION
    ic_horizon: int = 1
    min_abs_mean_rank_ic: Decimal = Decimal("0.02")
    min_abs_ic_ir: Decimal = Decimal("0.30")
    min_positive_rate: Decimal = Decimal("0.55")
    min_ic_observations: int = 10
    min_surviving_factors: int = 3
    top_k: int | None = None
    weighting: Literal["equal"] = "equal"
    allow_flip_min_training_days: int = 60

    def __post_init__(self) -> None:
        if self.strategy_id != POLICY_VERSION:
            raise ValueError(f"unknown selection strategy: {self.strategy_id}")
        if self.ic_horizon != 1:
            raise ValueError("training_ic_v1 supports ic_horizon=1 only")
        if self.weighting != "equal":
            raise ValueError("training_ic_v1 supports equal weighting only")
        if self.top_k is not None and self.top_k < 1:
            raise ValueError("top_k must be >= 1 when provided")

    def config_hash(self, candidates: list[FactorScoreSpec]) -> str:
        """Deterministic short hash binding policy + candidate pool identity.

        Requested weights are deliberately excluded: they have no influence on
        selection, so changing them must not fork a new artifact family.
        """
        payload = {
            "strategy_id": self.strategy_id,
            "ic_horizon": self.ic_horizon,
            "min_abs_mean_rank_ic": str(self.min_abs_mean_rank_ic),
            "min_abs_ic_ir": str(self.min_abs_ic_ir),
            "min_positive_rate": str(self.min_positive_rate),
            "min_ic_observations": self.min_ic_observations,
            "min_surviving_factors": self.min_surviving_factors,
            "top_k": self.top_k,
            "weighting": self.weighting,
            "allow_flip_min_training_days": self.allow_flip_min_training_days,
            "candidates": sorted(
                {
                    f"{spec.factor_name}@{spec.factor_version}:higher={spec.higher_is_better}"
                    for spec in candidates
                }
            ),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]

    def to_snapshot(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "ic_horizon": self.ic_horizon,
            "min_abs_mean_rank_ic": str(self.min_abs_mean_rank_ic),
            "min_abs_ic_ir": str(self.min_abs_ic_ir),
            "min_positive_rate": str(self.min_positive_rate),
            "min_ic_observations": self.min_ic_observations,
            "min_surviving_factors": self.min_surviving_factors,
            "top_k": self.top_k,
            "weighting": self.weighting,
            "allow_flip_min_training_days": self.allow_flip_min_training_days,
        }


def build_training_selection_policy(
    name: str,
    *,
    min_surviving_factors: int | None = None,
) -> TrainingSelectionPolicy:
    if name != POLICY_VERSION:
        raise ValueError(f"unknown selection_policy {name!r}; supported: {POLICY_VERSION}")
    if min_surviving_factors is not None:
        return TrainingSelectionPolicy(min_surviving_factors=min_surviving_factors)
    return TrainingSelectionPolicy()


@dataclass(frozen=True)
class FactorSelectionStats:
    factor_name: str
    factor_version: str
    prior_higher_is_better: bool
    observations: int
    mean_rank_ic: Decimal | None
    ic_ir: Decimal | None
    positive_rate: Decimal | None
    decision: Literal["maintained", "flipped", "rejected"]
    direction_higher_is_better: bool | None
    reason: str | None = None

    def to_snapshot(self) -> dict[str, object]:
        return {
            "factor_name": self.factor_name,
            "factor_version": self.factor_version,
            "prior_higher_is_better": self.prior_higher_is_better,
            "observations": self.observations,
            "mean_rank_ic": (None if self.mean_rank_ic is None else str(self.mean_rank_ic)),
            "ic_ir": None if self.ic_ir is None else str(self.ic_ir),
            "positive_rate": (None if self.positive_rate is None else str(self.positive_rate)),
            "decision": self.decision,
            "direction_higher_is_better": self.direction_higher_is_better,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TrainingSelectionResult:
    policy: TrainingSelectionPolicy
    config_hash: str
    training_start_date: object
    training_end_date: object
    training_open_dates: int
    selected_specs: list[FactorScoreSpec] = field(default_factory=list)
    stats: list[FactorSelectionStats] = field(default_factory=list)
    dropped_candidates: list[dict[str, str]] = field(default_factory=list)
    price_source: dict[str, str] = field(default_factory=dict)
    diagnostics: dict[str, object] | None = None

    def summary(self) -> dict[str, object]:
        summary = {
            "strategy_id": self.policy.strategy_id,
            "config_hash": self.config_hash,
            "training_start_date": self.training_start_date.isoformat(),
            "training_end_date": self.training_end_date.isoformat(),
            "training_open_dates": self.training_open_dates,
            "selected": [
                {
                    "factor_name": spec.factor_name,
                    "factor_version": spec.factor_version,
                    "higher_is_better": spec.higher_is_better,
                }
                for spec in self.selected_specs
            ],
            "stats": [stat.to_snapshot() for stat in self.stats],
            "dropped_candidates": list(self.dropped_candidates),
            "price_source": dict(self.price_source),
        }
        if self.diagnostics is not None:
            summary["diagnostics"] = self.diagnostics
        return summary


def _quantize(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


def select_training_factors(
    *,
    policy: TrainingSelectionPolicy,
    candidates: list[FactorScoreSpec],
    rows_by_spec: dict[FactorScoreSpec, list[SimpleNamespace]],
    qfq_closes: dict[str, dict[object, Decimal]],
    training_open_dates: list[object],
    training_start_date: object,
    training_end_date: object,
    price_source: dict[str, str] | None = None,
    diagnostics_policy: TrainingDiagnosticsPolicy | None = None,
) -> TrainingSelectionResult:
    """Score every candidate on the training window and apply the policy gate.

    ``qfq_closes`` maps symbol -> {trade_date -> close}; the IC horizon uses
    the SH calendar pairs ``(t, next_open(t))`` inside the training window
    only.  The final training date never contributes an observation because
    its forward close is unknown at selection time.
    """
    if policy.ic_horizon != 1:
        raise ValueError("training_ic_v1 supports ic_horizon=1 only")
    horizon_dates = {
        training_open_dates[i]: training_open_dates[i + 1]
        for i in range(len(training_open_dates) - 1)
    }
    forward_returns_by_date: dict[object, dict[str, Decimal]] = {}
    for signal_date, target_date in horizon_dates.items():
        returns: dict[str, Decimal] = {}
        for symbol, closes in qfq_closes.items():
            base = closes.get(signal_date)
            target = closes.get(target_date)
            if base is None or target is None or base <= 0:
                continue
            returns[symbol] = target / base - Decimal(1)
        forward_returns_by_date[signal_date] = returns

    stats: list[FactorSelectionStats] = []
    dropped: list[dict[str, str]] = []
    evidence_by_identity: dict[tuple[str, str], list[DailyRankICEvidence]] = {}
    specs_by_identity: dict[tuple[str, str], FactorScoreSpec] = {}
    for spec in candidates:
        specs_by_identity[(spec.factor_name, spec.factor_version)] = spec
        rows = rows_by_spec.get(spec)
        if not rows:
            dropped.append(
                {
                    "factor_name": spec.factor_name,
                    "factor_version": spec.factor_version,
                    "reason": "no_factor_values_in_training_window",
                }
            )
            if diagnostics_policy is not None:
                evidence_by_identity[(spec.factor_name, spec.factor_version)] = [
                    DailyRankICEvidence(
                        trade_date=signal_date,
                        rank_ic=None,
                        common_symbol_count=0,
                        status="no_factor_values",
                    )
                    for signal_date in training_open_dates[:-1]
                    if isinstance(signal_date, date)
                ]
            continue
        values_by_date: dict[object, dict[str, Decimal]] = {}
        for row in rows:
            values_by_date.setdefault(row.trade_date, {})[row.symbol] = row.value

        daily_ics: list[Decimal] = []
        daily_evidence: list[DailyRankICEvidence] = []
        for signal_date in training_open_dates[:-1]:
            factor_values = values_by_date.get(signal_date)
            forward_returns = forward_returns_by_date.get(signal_date, {})
            if not factor_values:
                if diagnostics_policy is not None and isinstance(signal_date, date):
                    daily_evidence.append(
                        DailyRankICEvidence(signal_date, None, 0, "no_factor_values")
                    )
                continue
            common_count = len(set(factor_values) & set(forward_returns))
            daily_ic = rank_ic.rank_information_coefficient(factor_values, forward_returns)
            if daily_ic is not None:
                daily_ics.append(daily_ic)
                status = "recorded"
            elif not forward_returns:
                status = "missing_forward_prices"
            elif common_count < 2:
                status = "insufficient_pairs"
            else:
                status = "zero_dispersion"
            if diagnostics_policy is not None and isinstance(signal_date, date):
                daily_evidence.append(
                    DailyRankICEvidence(signal_date, daily_ic, common_count, status)
                )

        if diagnostics_policy is not None:
            evidence_by_identity[(spec.factor_name, spec.factor_version)] = daily_evidence

        mean_ic = _quantize(rank_ic.mean(daily_ics))
        ic_ir = _quantize(
            _ratio(
                rank_ic.mean(daily_ics),
                rank_ic.standard_deviation(daily_ics),
            )
        )
        if mean_ic is None:
            direction_higher = True
        else:
            direction_higher = mean_ic > 0
        aligned_positive = [ic if direction_higher else -ic for ic in daily_ics]
        positive_rate = _quantize(
            _ratio(
                Decimal(sum(1 for ic in aligned_positive if ic > 0)),
                Decimal(len(aligned_positive)),
            )
            if aligned_positive
            else None
        )

        decision, reason = _apply_gate(
            policy=policy,
            spec=spec,
            mean_ic=mean_ic,
            ic_ir=ic_ir,
            positive_rate=positive_rate,
            observations=len(daily_ics),
            direction_higher=direction_higher,
            training_days=len(training_open_dates),
        )
        stats.append(
            FactorSelectionStats(
                factor_name=spec.factor_name,
                factor_version=spec.factor_version,
                prior_higher_is_better=spec.higher_is_better,
                observations=len(daily_ics),
                mean_rank_ic=mean_ic,
                ic_ir=ic_ir,
                positive_rate=positive_rate,
                decision=decision,
                direction_higher_is_better=(direction_higher if decision != "rejected" else None),
                reason=reason,
            )
        )

    survivors = [stat for stat in stats if stat.decision != "rejected"]
    survivors.sort(
        key=lambda stat: (
            -(abs(stat.ic_ir) if stat.ic_ir is not None else Decimal(0)),
            -(abs(stat.mean_rank_ic) if stat.mean_rank_ic is not None else Decimal(0)),
            stat.factor_name,
            stat.factor_version,
        )
    )
    if policy.top_k is not None:
        survivors = survivors[: policy.top_k]
    if len(survivors) < policy.min_surviving_factors:
        detail = "; ".join(f"{stat.factor_name}: {stat.reason or stat.decision}" for stat in stats)
        raise ValueError(
            "training window selection left fewer than "
            f"{policy.min_surviving_factors} surviving factors "
            f"({len(survivors)} survived between "
            f"{training_start_date.isoformat()} and "
            f"{training_end_date.isoformat()}): {detail}"
        )

    selected_specs: list[FactorScoreSpec] = []
    for stat in survivors:
        spec = specs_by_identity[(stat.factor_name, stat.factor_version)]
        selected_specs.append(
            FactorScoreSpec(
                factor_name=spec.factor_name,
                factor_version=spec.factor_version,
                weight=Decimal("1"),
                higher_is_better=bool(stat.direction_higher_is_better),
            )
        )
    diagnostics = None
    if diagnostics_policy is not None:
        eligible_dates = [
            value for value in training_open_dates[:-1] if isinstance(value, date)
        ]
        price_dates = [value for value in training_open_dates if isinstance(value, date)]
        diagnostics = build_training_diagnostics(
            policy=diagnostics_policy,
            candidates=candidates,
            eligible_dates=eligible_dates,
            price_dates=price_dates,
            evidence_by_identity=evidence_by_identity,
            qfq_closes=qfq_closes,
            price_source=dict(price_source or {}),
        )
    return TrainingSelectionResult(
        policy=policy,
        config_hash=policy.config_hash(candidates),
        training_start_date=training_start_date,
        training_end_date=training_end_date,
        training_open_dates=len(training_open_dates),
        selected_specs=selected_specs,
        stats=stats,
        dropped_candidates=dropped,
        price_source=dict(price_source or {}),
        diagnostics=diagnostics,
    )


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _apply_gate(
    *,
    policy: TrainingSelectionPolicy,
    spec: FactorScoreSpec,
    mean_ic: Decimal | None,
    ic_ir: Decimal | None,
    positive_rate: Decimal | None,
    observations: int,
    direction_higher: bool,
    training_days: int,
) -> tuple[Literal["maintained", "flipped", "rejected"], str | None]:
    if observations < policy.min_ic_observations:
        return "rejected", f"insufficient_ic_observations({observations})"
    if mean_ic is None or ic_ir is None or positive_rate is None:
        return "rejected", "insufficient_ic_dispersion"
    if abs(mean_ic) < policy.min_abs_mean_rank_ic:
        return "rejected", f"mean_rank_ic_below_threshold({mean_ic})"
    if abs(ic_ir) < policy.min_abs_ic_ir:
        return "rejected", f"ic_ir_below_threshold({ic_ir})"
    if positive_rate < policy.min_positive_rate:
        return "rejected", f"positive_rate_below_threshold({positive_rate})"
    conflicts_with_prior = direction_higher != spec.higher_is_better
    if not conflicts_with_prior:
        return "maintained", None
    if training_days >= policy.allow_flip_min_training_days:
        return "flipped", None
    return (
        "rejected",
        "direction_conflicts_prior_without_sufficient_training_days",
    )
