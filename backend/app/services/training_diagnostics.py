from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from app.strategies import FactorScoreSpec

DIAGNOSTICS_VERSION = "training_ic_hac_bh_v1"
MAX_DIAGNOSTICS_BYTES = 2_000_000
_QUANT = Decimal("0.0000000001")


@dataclass(frozen=True)
class TrainingDiagnosticsPolicy:
    version: str = DIAGNOSTICS_VERSION
    hac_lag: int = 1
    minimum_observations: int = 10
    fdr_alpha: Decimal = Decimal("0.05")

    def __post_init__(self) -> None:
        if self.version != DIAGNOSTICS_VERSION:
            raise ValueError(f"unknown training diagnostics policy: {self.version}")
        if self.hac_lag != 1:
            raise ValueError("training_ic_hac_bh_v1 supports hac_lag=1 only")

    def config_hash(self, candidates: list[FactorScoreSpec]) -> str:
        identities = _candidate_identities(candidates)
        payload = {**self.to_snapshot(), "candidates": identities}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]

    def to_snapshot(self) -> dict[str, object]:
        return {
            "version": self.version,
            "hac_lag": self.hac_lag,
            "minimum_observations": self.minimum_observations,
            "fdr_alpha": str(self.fdr_alpha),
            "null_hypothesis": "two_sided_mean_rank_ic_equals_zero",
            "missing_date_policy": "fixed_trading_date_grid_no_compression",
            "fdr_family_policy": "declared_candidate_family_v1",
        }


@dataclass(frozen=True)
class DailyRankICEvidence:
    trade_date: date
    rank_ic: Decimal | None
    common_symbol_count: int
    status: Literal[
        "recorded",
        "no_factor_values",
        "missing_forward_prices",
        "insufficient_pairs",
        "zero_dispersion",
    ]

    def to_snapshot(self) -> dict[str, object]:
        return {
            "trade_date": self.trade_date.isoformat(),
            "rank_ic": None if self.rank_ic is None else str(self.rank_ic),
            "common_symbol_count": self.common_symbol_count,
            "status": self.status,
        }


def build_training_diagnostics_policy(name: str | None) -> TrainingDiagnosticsPolicy | None:
    if name is None:
        return None
    if name != DIAGNOSTICS_VERSION:
        raise ValueError(
            f"unknown training diagnostics policy {name!r}; supported: {DIAGNOSTICS_VERSION}"
        )
    return TrainingDiagnosticsPolicy()


def build_training_diagnostics(
    *,
    policy: TrainingDiagnosticsPolicy,
    candidates: list[FactorScoreSpec],
    eligible_dates: list[date],
    price_dates: list[date],
    evidence_by_identity: dict[tuple[str, str], list[DailyRankICEvidence]],
    qfq_closes: dict[str, dict[object, Decimal]],
    price_source: dict[str, str],
) -> dict[str, object]:
    identities = _candidate_identities(candidates)
    factors: list[dict[str, object]] = []
    p_values: dict[tuple[str, str], Decimal] = {}
    for factor_name, factor_version in identities:
        series = evidence_by_identity.get((factor_name, factor_version), [])
        diagnostic = _hac_diagnostic(policy=policy, series=series)
        if diagnostic["p_value"] is not None:
            p_values[(factor_name, factor_version)] = diagnostic["p_value"]  # type: ignore[assignment]
        factors.append(
            {
                "factor_name": factor_name,
                "factor_version": factor_version,
                **diagnostic,
                "rank_ic_series": [item.to_snapshot() for item in series],
            }
        )

    q_values = _benjamini_hochberg(
        p_values,
        declared_count=len(identities),
    )
    alpha = policy.fdr_alpha
    for factor in factors:
        identity = (str(factor["factor_name"]), str(factor["factor_version"]))
        q_value = q_values.get(identity)
        factor["q_value"] = q_value
        factor["reject_null"] = q_value is not None and q_value <= alpha
        for field_name in (
            "mean_rank_ic",
            "hac_standard_error",
            "z_score",
            "p_value",
            "q_value",
        ):
            value = factor[field_name]
            if isinstance(value, Decimal):
                factor[field_name] = str(value)

    evidence_payload = {
        "eligible_dates": [value.isoformat() for value in eligible_dates],
        "factors": [
            {
                "factor_name": factor["factor_name"],
                "factor_version": factor["factor_version"],
                "rank_ic_series": factor["rank_ic_series"],
            }
            for factor in factors
        ],
    }
    evidence_sha256 = _sha256(evidence_payload)
    testable_count = len(p_values)
    report = {
        "contract_version": DIAGNOSTICS_VERSION,
        "status": "recorded" if testable_count else "insufficient",
        "reason": (
            None
            if testable_count
            else "no candidate has enough valid daily RankIC evidence for HAC"
        ),
        "research_only": True,
        "selection_unchanged": True,
        "qualification_unchanged": True,
        "policy": {
            **policy.to_snapshot(),
            "config_hash": policy.config_hash(candidates),
        },
        "family": {
            "declared_count": len(identities),
            "testable_count": testable_count,
            "unavailable_count": len(identities) - testable_count,
            "correction": "benjamini_hochberg_declared_family_v1",
            "alpha": str(alpha),
        },
        "eligible_dates": [value.isoformat() for value in eligible_dates],
        "evidence_sha256": evidence_sha256,
        "price_snapshot": _price_snapshot(
            qfq_closes=qfq_closes,
            price_dates=price_dates,
            price_source=price_source,
        ),
        "factors": factors,
        "declarations": [
            (
                "p/q values diagnose the recorded training window only; "
                "they are not future-profit probabilities."
            ),
            (
                "The two-sided HAC normal approximation does not use test-window "
                "returns or change factor selection."
            ),
            (
                "BH uses the full declared candidate count; unavailable candidates "
                "are not assigned zero p-values."
            ),
            (
                "Repeated windows, repeated viewing, and earlier research attempts "
                "are outside this FDR family."
            ),
        ],
    }
    encoded = json.dumps(
        report, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    if len(encoded) > MAX_DIAGNOSTICS_BYTES:
        raise ValueError(
            f"training diagnostics exceed {MAX_DIAGNOSTICS_BYTES} byte artifact budget"
        )
    return report


def _hac_diagnostic(
    *,
    policy: TrainingDiagnosticsPolicy,
    series: list[DailyRankICEvidence],
) -> dict[str, object]:
    valid = [(index, item.rank_ic) for index, item in enumerate(series) if item.rank_ic is not None]
    observations = len(valid)
    base: dict[str, object] = {
        "status": "insufficient",
        "reason": None,
        "observations": observations,
        "hac_pair_count": 0,
        "mean_rank_ic": None,
        "hac_standard_error": None,
        "z_score": None,
        "p_value": None,
    }
    if observations < policy.minimum_observations:
        base["reason"] = f"insufficient_observations({observations})"
        return base

    values = [value for _, value in valid if value is not None]
    mean_value = sum(values, Decimal()) / Decimal(observations)
    residuals = {index: value - mean_value for index, value in valid if value is not None}
    variance_sum = sum((value * value for value in residuals.values()), Decimal())
    lag_product = Decimal()
    pair_count = 0
    for index, residual in residuals.items():
        prior = residuals.get(index - policy.hac_lag)
        if prior is None:
            continue
        lag_product += residual * prior
        pair_count += 1
    # Bartlett weight for L=1 is 1/2. The usual 2*w term therefore adds one
    # exact-grid lag product. Missing dates never become adjacent observations.
    long_run_sum = variance_sum + lag_product
    base["mean_rank_ic"] = _quantize(mean_value)
    base["hac_pair_count"] = pair_count
    if long_run_sum <= 0:
        base["reason"] = "non_positive_hac_variance"
        return base
    with localcontext() as context:
        context.prec = 28
        standard_error = long_run_sum.sqrt() / Decimal(observations)
    if standard_error == 0:
        base["reason"] = "zero_hac_standard_error"
        return base
    z_score = mean_value / standard_error
    p_value = Decimal(str(math.erfc(abs(float(z_score)) / math.sqrt(2))))
    base.update(
        {
            "status": "recorded",
            "reason": None,
            "hac_standard_error": _quantize(standard_error),
            "z_score": _quantize(z_score),
            "p_value": _quantize(p_value),
        }
    )
    return base


def _benjamini_hochberg(
    p_values: dict[tuple[str, str], Decimal],
    *,
    declared_count: int,
) -> dict[tuple[str, str], Decimal]:
    if not p_values:
        return {}
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[tuple[str, str], Decimal] = {}
    running = Decimal(1)
    for rank_index in range(len(ordered) - 1, -1, -1):
        identity, p_value = ordered[rank_index]
        rank = Decimal(rank_index + 1)
        candidate = min(Decimal(1), p_value * Decimal(declared_count) / rank)
        running = min(running, candidate)
        adjusted[identity] = _quantize(running)
    return adjusted


def _candidate_identities(candidates: list[FactorScoreSpec]) -> list[tuple[str, str]]:
    identities = sorted((spec.factor_name, spec.factor_version) for spec in candidates)
    if len(set(identities)) != len(identities):
        raise ValueError("training diagnostics require unique factor name/version candidates")
    return identities


def _price_snapshot(
    *,
    qfq_closes: dict[str, dict[object, Decimal]],
    price_dates: list[date],
    price_source: dict[str, str],
) -> dict[str, object]:
    date_set = set(price_dates)
    digest = hashlib.sha256()
    row_count = 0
    for symbol in sorted(qfq_closes):
        closes = qfq_closes[symbol]
        for trade_date in sorted(
            value for value in closes if isinstance(value, date) and value in date_set
        ):
            record = json.dumps(
                [symbol, trade_date.isoformat(), str(closes[trade_date])],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            digest.update(record.encode("utf-8"))
            digest.update(b"\n")
            row_count += 1
    return {
        "row_count": row_count,
        "sha256": digest.hexdigest(),
        "source": dict(sorted(price_source.items())),
    }


def _sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)
