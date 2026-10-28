"""Versioned, fail-closed quality-value research screen; no trading signals.

Inputs are curated market/factor evidence, not raw vendor observations. The
screen checks their declared provenance and dates, not the source's truth.
"""

from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

D = Decimal
VERSION = "quality_value_attention_v1"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    value: Decimal
    source_ref: str = Field(min_length=1)
    known_on: date
    valid_through: date
    trusted: bool = False

    @model_validator(mode="after")
    def check_dates(self) -> "Evidence":
        if self.valid_through < self.known_on:
            raise ValueError("valid_through precedes known_on")
        if not self.source_ref.strip():
            raise ValueError("source_ref cannot be blank")
        return self


class Catalyst(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["capital_return", "orders", "industry_recovery", "earnings_revision"]
    evidence: Evidence
    verify_by: date
    thesis: str = Field(min_length=1)
    invalidation: str = Field(min_length=1)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=1)
    industry: str = Field(min_length=1)
    # Separate templates are required for financials and cyclical earnings.
    model: Literal["stable_nonfinancial", "financial", "cyclical", "unclassified"]
    attribution: Literal["A", "B", "C", "D", "unknown"]
    thesis: str | None = Field(default=None, min_length=1)
    invalidation: str | None = Field(default=None, min_length=1)
    metrics: dict[str, Evidence]
    catalysts: list[Catalyst] = Field(default_factory=list)


class ScreenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    candidates: list[Candidate]
    pause_new_entries: bool = False

    @model_validator(mode="after")
    def unique_symbols(self) -> "ScreenRequest":
        symbols = [c.symbol.strip().upper() for c in self.candidates]
        if any(not s for s in symbols) or len(symbols) != len(set(symbols)):
            raise ValueError("symbols must be nonblank and unique")
        return self


# All rates and percentiles are fractions, amounts are CNY, ages are days.
# Gate order: hard screen, quality, attribution, catalyst, score/selection.
HARD_GATES = {
    "market_cap": (">=", D("8000000000")),
    "listed_days": (">=", D("730")),
    "pe_positive": ("==", D(1)),
    "pb_positive": ("==", D(1)),
    "pe_history_percentile": ("<=", D("0.20")),
    "pb_history_percentile": ("<=", D("0.20")),
    "pe_industry_ratio": ("<", D(1)),
    "pb_industry_ratio": ("<", D(1)),
    "valuation_history_years": (">=", D(3)),
    "dividend_years": (">=", D(3)),
    "risk_clear": ("==", D(1)),
}
QUALITY_GATES = {
    "roe_mean_3y": (">=", D("0.12")),
    "net_income_sum_3y": (">", D(0)),
    "ocf_profit_3y": (">=", D("0.8")),
    "ocf_ttm": (">", D(0)),
    "deducted_profit_ttm": (">", D(0)),
    "deducted_profit_trend_clear": ("==", D(1)),
    "goodwill_equity": ("<", D("0.15")),
    "receivables_quality_clear": ("==", D(1)),
    "interest_debt_assets": ("<", D("0.40")),
    "unrestricted_cash_short_debt": (">=", D(1)),
    "controller_pledge": ("<", D("0.30")),
    "pledge_safety_clear": ("==", D(1)),
    "moat_stable": ("==", D(1)),
    "dividend_coverage_clear": ("==", D(1)),
}
ATTENTION = ("coverage_percentile", "institution_percentile", "turnover_percentile")
FLAGS = {
    "pe_positive", "pb_positive", "risk_clear", "deducted_profit_trend_clear",
    "receivables_quality_clear", "pledge_safety_clear", "moat_stable",
    "dividend_coverage_clear", "one_off_confirmed",
}


def _usable(evidence: Evidence | None, as_of: date) -> bool:
    return bool(
        evidence is not None and evidence.trusted
        and evidence.known_on <= as_of <= evidence.valid_through
    )


def _compare(value: Decimal, op: str, threshold: Decimal) -> bool:
    if op == ">=":
        return value >= threshold
    if op == ">":
        return value > threshold
    if op == "<=":
        return value <= threshold
    if op == "<":
        return value < threshold
    return value == threshold


def _clamp(value: Decimal) -> Decimal:
    return max(D(0), min(D(1), value))


def _evaluate(candidate: Candidate, as_of: date) -> dict:
    result = {
        "symbol": candidate.symbol.strip().upper(), "industry": candidate.industry,
        "status": "blocked", "stage": "data", "reasons": [],
        "score": None, "components": {}, "thesis": candidate.thesis,
        "invalidation": candidate.invalidation,
        "evidence": {k: v.model_dump(mode="json") for k, v in candidate.metrics.items()},
        "catalysts": [c.model_dump(mode="json") for c in candidate.catalysts],
    }
    if candidate.model != "stable_nonfinancial":
        result["reasons"] = ["unsupported_industry_model"]
        return result
    if not candidate.industry.strip() or candidate.industry.strip().lower() in {
        "unknown", "待定", "-",
    } or not (candidate.thesis or "").strip() or not (candidate.invalidation or "").strip():
        result["reasons"] = ["missing_industry_or_thesis"]
        return result

    values: dict[str, Decimal] = {}

    def load(names: list[str]) -> list[str]:
        errors = []
        for name in names:
            evidence = candidate.metrics.get(name)
            if not _usable(evidence, as_of):
                errors.append(f"missing_or_unusable:{name}")
                continue
            value = evidence.value
            if (
                (name in FLAGS and value not in (D(0), D(1)))
                or ("percentile" in name and not D(0) <= value <= D(1))
                or value < 0 and name not in {"ocf_ttm", "deducted_profit_ttm",
                                             "net_income_sum_3y", "roe_mean_3y"}
            ):
                errors.append(f"invalid_metric:{name}")
                continue
            values[name] = value
        return errors

    for stage, gates in (("hard_screen", HARD_GATES), ("quality", QUALITY_GATES)):
        names = list(gates)
        if stage == "hard_screen":
            names += ["dividend_yield", "amount_mean_20"]
        errors = load(names)
        failures = [f"failed:{name}" for name, (op, threshold) in gates.items()
                    if name in values and not _compare(values[name], op, threshold)]
        if stage == "hard_screen" and "market_cap" in values:
            white_chip = values["market_cap"] >= D("30000000000")
            if ("dividend_yield" in values and values["dividend_yield"]
                    < (D("0.03") if white_chip else D("0.02"))):
                failures.append("failed:dividend_yield")
            if "amount_mean_20" in values and values["amount_mean_20"] < D("50000000"):
                failures.append("failed:amount_mean_20")
        if failures:
            result.update(status="excluded", stage=stage, reasons=failures + errors)
            return result
        if errors:
            result.update(stage=stage, reasons=errors)
            return result

    if candidate.attribution == "D":
        result.update(status="excluded", stage="attribution", reasons=["broken_thesis"])
        return result
    if candidate.attribution == "unknown":
        result.update(stage="attribution", reasons=["attribution_unreviewed"])
        return result
    if candidate.attribution == "C":
        errors = load(["one_off_confirmed"])
        if errors or values["one_off_confirmed"] != 1:
            result.update(status="watch", stage="attribution",
                          reasons=errors or ["one_off_not_confirmed"])
            return result

    # Realized orders/capital return must be documented, not announced hopes.
    catalysts = [c for c in candidate.catalysts
                 if _usable(c.evidence, as_of) and c.evidence.value == 1
                 and as_of < c.verify_by and (c.verify_by - as_of).days <= 183
                 and c.thesis.strip() and c.invalidation.strip()]
    if not catalysts:
        result.update(status="watch", stage="catalyst", reasons=["no_verified_catalyst"])
        return result
    errors = load(list(ATTENTION))
    if errors:
        result.update(stage="attention", reasons=errors)
        return result
    attention = sum((values[k] for k in ATTENTION), D(0)) / 3
    if attention > D("0.40"):
        result.update(status="excluded", stage="attention", reasons=["attention_too_high"])
        return result
    components = {
        "valuation": D(25) * (1 - (values["pe_history_percentile"]
                                   + values["pb_history_percentile"]) / 2),
        "quality": D(25) * (_clamp(values["roe_mean_3y"] / D("0.20"))
                            + _clamp(values["ocf_profit_3y"] / D("1.2"))) / 2,
        "balance_sheet": D(15) * (
            _clamp(1 - values["interest_debt_assets"] / D("0.4"))
            + _clamp(1 - values["goodwill_equity"] / D("0.15"))
            + _clamp(values["unrestricted_cash_short_debt"] / 2)) / 3,
        "shareholder_return": D(10) * _clamp(values["dividend_yield"] / D("0.05")),
        "attention": D(10) * (1 - attention),
        "catalyst": D(15) if min((c.verify_by - as_of).days for c in catalysts) <= 92
        else D(10),
    }
    score = sum(components.values(), D(0))
    result.update(status="candidate" if score >= 70 else "watch", stage="score",
                  score=score, components=components,
                  reasons=[] if score >= 70 else ["score_below_70"])
    return result


def screen(request: ScreenRequest) -> dict:
    """Return an explainable shortlist, never orders or backtest eligibility."""
    rows = [_evaluate(c, request.as_of) for c in request.candidates]
    eligible = sorted((r for r in rows if r["status"] == "candidate"),
                      key=lambda r: (-r["score"], r["symbol"]))
    counts: dict[str, int] = {}
    selected = []
    for row in eligible:
        reason = None
        if request.pause_new_entries:
            reason = "new_entries_paused"
        elif len(selected) >= 8:
            reason = "outside_top_8"
        elif counts.get(row["industry"], 0) >= 2:
            reason = "industry_top_2_only"
        if reason:
            row.update(status="watch", stage="selection", reasons=[reason])
        else:
            row["status"] = "selected"
            selected.append(row["symbol"])
            counts[row["industry"]] = counts.get(row["industry"], 0) + 1
    return {
        "strategy": VERSION, "as_of": request.as_of, "research_only": True,
        "input_sha256": sha256(request.model_dump_json().encode()).hexdigest(),
        "historical_pit_certified": False, "backtest_eligible": False,
        "selected_symbols": selected,
        "diversification_ready": len(selected) >= 5 and len(counts) >= 3,
        "rows": sorted(rows, key=lambda r: r["symbol"]),
    }
