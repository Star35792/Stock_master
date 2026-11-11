"""Versioned research workload selection; never an allocation or return forecast."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation

VERSION = "value_research_shortlist_v2"
PARAMETERS = {
    "pe_max": "20",
    "pb_max": "2",
    "listed_days_min": 730,
    "amount_mean_20_min": "50000000",
    "positive_profit_years_required": 3,
    "net_income_ttm_min_exclusive": "0",
    "ma20_gap_max": "0.08",
    "roe_min_exclusive": "0",
    "roe_abnormal_max": "0.30",
    "cash_ratio_rank_cap": "2",
    "roe_rank_cap": "0.30",
    "max_candidates": 10,
    "max_per_industry": 2,
    "cyclical_industry_tokens": [
        "煤炭", "钢铁", "普钢", "特钢", "油气", "石油", "石化", "化工", "化学",
        "工业金属", "小金属", "贵金属", "能源金属", "有色金属", "航运", "房地产",
        "水泥", "玻璃", "玻纤", "造纸", "农化", "炼化", "冶钢", "油服", "工程机械",
    ],
    "ranking": [
        "pe_vendor_unverified/20+pb_vendor/2 ascending",
        "min(ocf_profit_3y,2) descending",
        "min(roe_reported_vendor,0.30) descending",
        "symbol ascending",
    ],
}
RANKING_DESCRIPTION = (
    "先按供应商PE/20+PB/2从低到高；相同则依次按三年现金流/利润（最多计2）、"
    "主期披露ROE（最多计30%）从高到低，最后按代码排序。每行业最多2只、总计最多10只。"
    "这是研究工作量排序，未验证收益优势；容量递延不代表股票不合格。"
)


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, (bool, float)):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _evaluate(row: dict) -> dict:
    diagnostics = row.get("research_diagnostics") or {}
    quality = diagnostics.get("quality") or {}
    trend = diagnostics.get("trend") or {}
    supplied = row.get("selection_inputs") or {}
    raw_numbers = {
        "pe": row.get("pe_vendor_unverified"),
        "pb": row.get("pb_vendor"),
        "roe": row.get("roe_reported_vendor"),
        "amount_mean_20": supplied.get("amount_mean_20"),
        "ma20_gap": trend.get("ma20_gap"),
        "ocf_profit_3y": quality.get("ocf_profit_3y"),
        "net_income_min_3y": quality.get("net_income_min_3y"),
        "net_income_ttm": quality.get("net_income_ttm"),
    }
    numbers = {name: _number(value) for name, value in raw_numbers.items()}
    metrics = {name: str(value) if value is not None else None
               for name, value in numbers.items()}
    metrics.update(
        listed_days=supplied.get("listed_days"),
        net_income_positive_years_3y=quality.get("net_income_positive_years_3y"),
        source_refs=list(supplied.get("source_refs") or []),
    )
    excluded, needs_review = [], []
    if row.get("status") != "ready_for_review":
        needs_review.append("shortlist_base_inputs_not_ready")
    if diagnostics.get("review_eligible") is not True:
        excluded.append("shortlist_research_conditions_not_met")
    if trend.get("status") != "improving" or quality.get("status") != "supported":
        excluded.append("shortlist_research_conditions_not_met")
    industry = str(row.get("industry") or "").strip()
    if not industry or industry.lower() in {"unknown", "待定", "-", "--", "—", "n/a"}:
        needs_review.append("shortlist_industry_missing")
    elif any(token in industry for token in PARAMETERS["cyclical_industry_tokens"]):
        needs_review.append("shortlist_cyclical_model_required")

    for name in numbers:
        if numbers[name] is None:
            needs_review.append(f"shortlist_{name}_missing_or_invalid")
    pe, pb, roe = numbers["pe"], numbers["pb"], numbers["roe"]
    for name, value in (("pe", pe), ("pb", pb)):
        if value is not None and not 0 < value <= Decimal(PARAMETERS[f"{name}_max"]):
            excluded.append(f"shortlist_{name}_outside_range")
    if roe is not None:
        if roe <= Decimal(PARAMETERS["roe_min_exclusive"]):
            excluded.append("shortlist_roe_nonpositive")
        elif roe > Decimal(PARAMETERS["roe_abnormal_max"]):
            needs_review.append("shortlist_roe_abnormal_review")
    newer_roe = _number(row.get("roe_newer_reported_vendor"))
    if row.get("roe_newer_report_date") is not None:
        if newer_roe is None:
            needs_review.append("shortlist_newer_roe_missing_or_invalid")
        elif newer_roe <= Decimal(PARAMETERS["roe_min_exclusive"]):
            excluded.append("shortlist_newer_roe_nonpositive")
        elif newer_roe > Decimal(PARAMETERS["roe_abnormal_max"]):
            needs_review.append("shortlist_newer_roe_abnormal_review")
        metrics["newer_roe"] = str(newer_roe) if newer_roe is not None else None
    elif row.get("roe_newer_reported_vendor") is not None:
        needs_review.append("shortlist_newer_roe_period_missing")
    listed = supplied.get("listed_days")
    if type(listed) is not int or listed < 0:
        needs_review.append("shortlist_listing_age_missing_or_invalid")
    elif listed < PARAMETERS["listed_days_min"]:
        excluded.append("shortlist_listing_age_insufficient")
    amount = numbers["amount_mean_20"]
    if amount is not None and amount < Decimal(PARAMETERS["amount_mean_20_min"]):
        excluded.append("shortlist_liquidity_insufficient")
    gap = numbers["ma20_gap"]
    if gap is not None and not 0 <= gap <= Decimal(PARAMETERS["ma20_gap_max"]):
        excluded.append("shortlist_trend_overheated_or_inconsistent")
    cash = numbers["ocf_profit_3y"]
    if cash is not None and cash < 1:
        excluded.append("shortlist_cash_flow_insufficient")
    positive_years = _number(quality.get("net_income_positive_years_3y"))
    minimum = numbers["net_income_min_3y"]
    if (positive_years is None or not 0 <= positive_years <= 3
            or positive_years != positive_years.to_integral_value()):
        needs_review.append("shortlist_profit_years_missing_or_invalid")
    elif positive_years != PARAMETERS["positive_profit_years_required"]:
        excluded.append("shortlist_annual_profit_not_continuously_positive")
    if minimum is not None and minimum <= 0:
        excluded.append("shortlist_annual_profit_not_continuously_positive")
    profit_ttm = numbers["net_income_ttm"]
    if profit_ttm is not None and profit_ttm <= Decimal(PARAMETERS["net_income_ttm_min_exclusive"]):
        excluded.append("shortlist_net_income_ttm_nonpositive")
    reasons = list(dict.fromkeys(needs_review + excluded))
    return {
        "status": "needs_review" if needs_review else "excluded" if excluded else
        "qualified_deferred",
        "qualified": not reasons, "selected": False, "rank": None,
        "reason_codes": reasons, "metrics": metrics,
    }


def build_shortlist(rows: list[dict], *, global_reasons: list[str]) -> dict:
    """Annotate full rows, retaining all exclusions and capacity deferrals.

    The caller must keep the read-time freshness/version gate in addition to
    this report-time gate. No number-of-candidates target relaxes any threshold.
    """
    symbols = [str(row.get("symbol") or "").strip().upper() for row in rows]
    if any(not symbol for symbol in symbols) or len(symbols) != len(set(symbols)):
        raise ValueError("shortlist requires unique nonblank symbols")
    qualified = []
    for row, symbol in zip(rows, symbols, strict=True):
        result = _evaluate(row)
        if global_reasons:
            result.update(status="blocked", qualified=False, selected=False, rank=None,
                          reason_codes=["shortlist_report_blocked", *global_reasons])
        row["shortlist"] = result
        if result["qualified"]:
            metrics = result["metrics"]
            normalized_value = Decimal(metrics["pe"]) / Decimal(PARAMETERS["pe_max"]) \
                + Decimal(metrics["pb"]) / Decimal(PARAMETERS["pb_max"])
            metrics["normalized_valuation"] = str(normalized_value)
            qualified.append((
                normalized_value,
                -min(Decimal(metrics["ocf_profit_3y"]),
                     Decimal(PARAMETERS["cash_ratio_rank_cap"])),
                -min(Decimal(metrics["roe"]), Decimal(PARAMETERS["roe_rank_cap"])),
                symbol, row,
            ))
    qualified.sort(key=lambda item: item[:4])
    industries, selected = Counter(), []
    for rank, (_, _, _, symbol, row) in enumerate(qualified, 1):
        result = row["shortlist"]
        result["rank"] = rank
        industry = str(row["industry"]).strip()
        if industries[industry] >= PARAMETERS["max_per_industry"]:
            result["reason_codes"] = ["shortlist_industry_capacity_deferred"]
        elif len(selected) >= PARAMETERS["max_candidates"]:
            result["reason_codes"] = ["shortlist_total_capacity_deferred"]
        else:
            industries[industry] += 1
            selected.append(symbol)
            result.update(status="selected", selected=True, reason_codes=[])
    counts = Counter(code for row in rows for code in row["shortlist"]["reason_codes"])
    return {
        "version": VERSION, "parameters": deepcopy(PARAMETERS),
        "qualified_count": len(qualified), "shortlist_count": len(selected),
        "ordered_symbols": selected, "reason_counts": dict(sorted(counts.items())),
        "ranking_description": RANKING_DESCRIPTION,
    }


def analyze_ttm_rule_impact(rows: list[dict], *, global_reasons: list[str]) -> dict:
    """Compare v2 with a same-input counterfactual that omits only the TTM gate."""
    ttm_codes = {
        "shortlist_net_income_ttm_nonpositive",
        "shortlist_net_income_ttm_missing_or_invalid",
    }
    affected = {
        code: sorted(
            str(row.get("symbol") or "").strip().upper()
            for row in rows
            if code in (row.get("shortlist") or {}).get("reason_codes", [])
        )
        for code in sorted(ttm_codes)
    }
    counterfactual_rows = deepcopy(rows)
    for row in counterfactual_rows:
        reasons = set((row.get("shortlist") or {}).get("reason_codes", []))
        if reasons & ttm_codes:
            diagnostics = row.setdefault("research_diagnostics", {})
            diagnostics.setdefault("quality", {})["net_income_ttm"] = "1"
    counterfactual = build_shortlist(counterfactual_rows, global_reasons=global_reasons)
    current_selected = {
        str(row.get("symbol") or "").strip().upper()
        for row in rows
        if (row.get("shortlist") or {}).get("selected") is True
    }
    counterfactual_selected = set(counterfactual["ordered_symbols"])
    return {
        "comparison": "same_input_without_net_income_ttm_gate",
        "v1_equivalent_qualified_count": counterfactual["qualified_count"],
        "v1_equivalent_shortlist_count": counterfactual["shortlist_count"],
        "v1_equivalent_ordered_symbols": counterfactual["ordered_symbols"],
        "removed_from_qualified_by_v2_count": (
            counterfactual["qualified_count"]
            - sum((row.get("shortlist") or {}).get("qualified") is True for row in rows)
        ),
        "removed_from_shortlist_by_v2": sorted(counterfactual_selected - current_selected),
        "reason_symbols": affected,
        "reason_counts": {code: len(symbols) for code, symbols in affected.items()},
        "mixed_with_other_reasons_count": sum(
            bool(reasons & ttm_codes) and bool(reasons - ttm_codes)
            for row in rows
            if (reasons := set((row.get("shortlist") or {}).get("reason_codes", [])))
        ),
        "research_only": True,
    }
