from copy import deepcopy
from decimal import Decimal
from random import Random

import pytest

from app.services.price_value_shortlist import (
    PARAMETERS,
    VERSION,
    analyze_ttm_rule_impact,
    build_shortlist,
)


def _row(symbol="600001.SH", *, industry="家用电器", pe="10", pb="1"):
    return {
        "symbol": symbol, "industry": industry, "status": "ready_for_review",
        "pe_vendor_unverified": pe, "pb_vendor": pb,
        "roe_reported_vendor": "0.08", "roe_newer_report_date": None,
        "roe_newer_reported_vendor": None,
        "selection_inputs": {
            "listed_days": 730, "amount_mean_20": "50000000",
            "source_refs": ["market.daily_bar:fixture:20sessions"],
        },
        "research_diagnostics": {
            "review_eligible": True,
            "trend": {"status": "improving", "ma20_gap": "0.08"},
            "quality": {"status": "supported", "ocf_profit_3y": "1",
                        "net_income_positive_years_3y": 3, "net_income_min_3y": "100",
                        "net_income_ttm": "100"},
        },
    }


def test_inclusive_boundaries_and_full_policy_are_auditable():
    rows = [_row(pe="20", pb="2")]
    result = build_shortlist(rows, global_reasons=[])
    assert result["version"] == VERSION
    assert result["ordered_symbols"] == ["600001.SH"]
    assert result["qualified_count"] == result["shortlist_count"] == 1
    assert result["parameters"] == PARAMETERS
    assert rows[0]["shortlist"]["metrics"]["normalized_valuation"] == "2"
    assert rows[0]["shortlist"]["rank"] == 1
    assert rows[0]["shortlist"]["metrics"]["source_refs"]
    result["parameters"]["max_candidates"] = 999
    assert PARAMETERS["max_candidates"] == 10


@pytest.mark.parametrize("value", [3, "3", Decimal("3")])
def test_profit_years_supports_exact_diagnostic_number_contract(value):
    row = _row()
    row["research_diagnostics"]["quality"]["net_income_positive_years_3y"] = value
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 1


@pytest.mark.parametrize("value", [True, 3.0, "2.5", "NaN", "4", "-1"])
def test_profit_years_rejects_nonexact_or_out_of_range_values(value):
    row = _row()
    row["research_diagnostics"]["quality"]["net_income_positive_years_3y"] = value
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 0
    assert "shortlist_profit_years_missing_or_invalid" in row["shortlist"]["reason_codes"]


@pytest.mark.parametrize("field,value,reason", [
    ("pe_vendor_unverified", "20.00000001", "shortlist_pe_outside_range"),
    ("pb_vendor", "2.00000001", "shortlist_pb_outside_range"),
    ("pe_vendor_unverified", "0", "shortlist_pe_outside_range"),
    ("pb_vendor", "-1", "shortlist_pb_outside_range"),
    ("roe_reported_vendor", "0", "shortlist_roe_nonpositive"),
    ("roe_reported_vendor", "0.30000001", "shortlist_roe_abnormal_review"),
    ("industry", "煤炭开采", "shortlist_cyclical_model_required"),
    ("industry", "基础化学", "shortlist_cyclical_model_required"),
    ("industry", "航运港口", "shortlist_cyclical_model_required"),
    ("industry", "", "shortlist_industry_missing"),
    ("status", "needs_review", "shortlist_base_inputs_not_ready"),
])
def test_top_level_gate_failures_are_not_replaced_to_fill_capacity(field, value, reason):
    row = _row()
    row[field] = value
    result = build_shortlist([row], global_reasons=[])
    assert result["qualified_count"] == result["shortlist_count"] == 0
    assert reason in row["shortlist"]["reason_codes"]
    assert row["shortlist"]["rank"] is None


@pytest.mark.parametrize("bad", [None, True, 10.0, "NaN", "Infinity", "-Infinity", "bad"])
def test_invalid_numbers_never_receive_default_scores(bad):
    row = _row(pe=bad)
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 0
    assert "shortlist_pe_missing_or_invalid" in row["shortlist"]["reason_codes"]
    assert "normalized_valuation" not in row["shortlist"]["metrics"]


@pytest.mark.parametrize("section,field,value,reason", [
    ("selection_inputs", "listed_days", 729, "shortlist_listing_age_insufficient"),
    ("selection_inputs", "listed_days", True, "shortlist_listing_age_missing_or_invalid"),
    ("selection_inputs", "amount_mean_20", "49999999.9999", "shortlist_liquidity_insufficient"),
    ("trend", "ma20_gap", "0.08000001", "shortlist_trend_overheated_or_inconsistent"),
    ("trend", "ma20_gap", "-0.01", "shortlist_trend_overheated_or_inconsistent"),
    ("quality", "net_income_positive_years_3y", 2,
     "shortlist_annual_profit_not_continuously_positive"),
    ("quality", "net_income_positive_years_3y", None,
     "shortlist_profit_years_missing_or_invalid"),
    ("quality", "net_income_min_3y", "0", "shortlist_annual_profit_not_continuously_positive"),
    ("quality", "net_income_min_3y", None, "shortlist_net_income_min_3y_missing_or_invalid"),
    ("quality", "ocf_profit_3y", "0.99999", "shortlist_cash_flow_insufficient"),
])
def test_nested_gates(section, field, value, reason):
    row = _row()
    target = row[section] if section == "selection_inputs" \
        else row["research_diagnostics"][section]
    target[field] = value
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 0
    assert reason in row["shortlist"]["reason_codes"]


def test_nonpositive_newer_roe_cannot_hide_behind_positive_common_period():
    row = _row()
    row.update(roe_newer_report_date="2026-09-30", roe_newer_reported_vendor="-0.01")
    assert build_shortlist([row], global_reasons=[])["qualified_count"] == 0
    assert "shortlist_newer_roe_nonpositive" in row["shortlist"]["reason_codes"]
    row["roe_newer_reported_vendor"] = None
    assert build_shortlist([row], global_reasons=[])["qualified_count"] == 0
    assert "shortlist_newer_roe_missing_or_invalid" in row["shortlist"]["reason_codes"]
    row["roe_newer_reported_vendor"] = "0.01"
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 1


@pytest.mark.parametrize("industry", [
    "水泥制品", "玻璃制造", "玻纤制造", "造纸", "农化制品", "炼化及贸易",
    "冶钢原料", "油服工程", "工程机械",
])
def test_cyclical_subindustry_synonyms_require_separate_review(industry):
    row = _row(industry=industry)
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 0
    assert row["shortlist"]["status"] == "needs_review"
    assert "shortlist_cyclical_model_required" in row["shortlist"]["reason_codes"]


def test_abnormal_newer_roe_cannot_hide_behind_normal_common_period():
    row = _row()
    row.update(roe_newer_report_date="2026-09-30", roe_newer_reported_vendor="0.31")
    result = build_shortlist([row], global_reasons=[])
    assert result["qualified_count"] == result["shortlist_count"] == 0
    assert row["shortlist"]["status"] == "needs_review"
    assert row["shortlist"]["selected"] is False
    assert "shortlist_newer_roe_abnormal_review" in row["shortlist"]["reason_codes"]
    row["roe_newer_reported_vendor"] = "0.30"
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 1


def test_original_diagnostics_and_global_gate_cannot_be_bypassed():
    row = _row()
    row["research_diagnostics"]["review_eligible"] = False
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 0
    row["research_diagnostics"]["review_eligible"] = True
    row["research_diagnostics"]["quality"]["status"] = "unknown"
    assert build_shortlist([row], global_reasons=[])["shortlist_count"] == 0
    row["research_diagnostics"]["quality"]["status"] = "supported"
    original = deepcopy(row["research_diagnostics"])
    result = build_shortlist([row], global_reasons=["input_batch_unverified"])
    assert result["qualified_count"] == result["shortlist_count"] == 0
    assert row["shortlist"]["status"] == "blocked"
    assert row["shortlist"]["reason_codes"] == [
        "shortlist_report_blocked", "input_batch_unverified",
    ]
    assert row["research_diagnostics"] == original


def test_exact_decimal_ranking_with_cash_and_roe_caps_and_symbol_tie_break():
    rows = [_row(f"60000{i}.SH", industry=f"行业{i}") for i in range(1, 7)]
    # The tiny Decimal difference is real; no float rounding should erase it.
    rows[0]["pe_vendor_unverified"] = "10.000000000000000001"
    rows[1]["research_diagnostics"]["quality"]["ocf_profit_3y"] = "1.1"
    rows[2]["research_diagnostics"]["quality"]["ocf_profit_3y"] = "2"
    rows[3]["research_diagnostics"]["quality"]["ocf_profit_3y"] = "9999"
    rows[4]["research_diagnostics"]["quality"]["ocf_profit_3y"] = "2"
    rows[4]["roe_reported_vendor"] = Decimal("0.09")
    result = build_shortlist(rows, global_reasons=[])
    assert result["ordered_symbols"] == [
        "600005.SH", "600003.SH", "600004.SH", "600002.SH", "600006.SH", "600001.SH",
    ]


def test_industry_and_total_capacity_defer_qualified_names_and_leave_rank_gaps():
    rows = [_row(f"600{i:03}.SH", industry="同一行业" if i < 5 else f"行业{i}")
            for i in range(15)]
    result = build_shortlist(rows, global_reasons=[])
    assert result["qualified_count"] == 15
    assert result["shortlist_count"] == 10
    assert result["ordered_symbols"][:3] == ["600000.SH", "600001.SH", "600005.SH"]
    assert rows[2]["shortlist"]["status"] == "qualified_deferred"
    assert rows[2]["shortlist"]["qualified"] is True
    assert rows[2]["shortlist"]["reason_codes"] == ["shortlist_industry_capacity_deferred"]
    assert rows[5]["shortlist"]["rank"] == 6
    assert rows[-1]["shortlist"]["reason_codes"] == ["shortlist_total_capacity_deferred"]
    permuted = deepcopy(rows)
    Random(12).shuffle(permuted)
    reordered = build_shortlist(permuted, global_reasons=[])
    assert reordered == result
    assert {r["symbol"]: r["shortlist"] for r in permuted} == {
        r["symbol"]: r["shortlist"] for r in rows
    }


def test_no_candidates_is_valid_and_duplicates_are_rejected():
    empty = build_shortlist([], global_reasons=[])
    assert empty["qualified_count"] == empty["shortlist_count"] == 0
    assert empty["ordered_symbols"] == []
    with pytest.raises(ValueError, match="unique"):
        build_shortlist([_row(), _row()], global_reasons=[])


@pytest.mark.parametrize("value,status,reason", [
    ("0", "excluded", "shortlist_net_income_ttm_nonpositive"),
    ("-30", "excluded", "shortlist_net_income_ttm_nonpositive"),
    (None, "needs_review", "shortlist_net_income_ttm_missing_or_invalid"),
    ("NaN", "needs_review", "shortlist_net_income_ttm_missing_or_invalid"),
])
def test_three_profitable_years_do_not_override_profit_ttm(value, status, reason):
    row = _row()
    row["research_diagnostics"]["quality"]["net_income_ttm"] = value
    result = build_shortlist([row], global_reasons=[])
    assert result["shortlist_count"] == 0
    assert row["shortlist"]["status"] == status
    assert reason in row["shortlist"]["reason_codes"]


def test_ttm_rule_impact_uses_same_inputs_and_separates_missing_from_loss():
    positive = _row("600001.SH", industry="行业1")
    loss = _row("600002.SH", industry="行业2")
    missing = _row("600003.SH", industry="行业3")
    loss["research_diagnostics"]["quality"]["net_income_ttm"] = "0"
    missing["research_diagnostics"]["quality"]["net_income_ttm"] = None
    rows = [positive, loss, missing]
    current = build_shortlist(rows, global_reasons=[])
    impact = analyze_ttm_rule_impact(rows, global_reasons=[])
    assert current["qualified_count"] == 1
    assert impact["v1_equivalent_qualified_count"] == 3
    assert impact["reason_counts"] == {
        "shortlist_net_income_ttm_missing_or_invalid": 1,
        "shortlist_net_income_ttm_nonpositive": 1,
    }
    assert impact["removed_from_qualified_by_v2_count"] == 2
    assert impact["mixed_with_other_reasons_count"] == 0
