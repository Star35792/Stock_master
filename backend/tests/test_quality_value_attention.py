import json
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.strategies.quality_value_attention import (
    Candidate,
    Catalyst,
    Evidence,
    ScreenRequest,
    screen,
)

DAY = date(2026, 10, 28)


def evidence(value="1", **overrides):
    return Evidence(value=Decimal(value), source_ref="market:test-fixture:not-real",
                    known_on=date(2026, 10, 20), valid_through=date(2026, 12, 31),
                    trusted=True, **overrides)


def candidate(symbol="AAA", industry="食品"):
    values = {
        "market_cap": "40000000000", "listed_days": "2000",
        "pe_positive": "1", "pb_positive": "1", "pe_history_percentile": "0.1",
        "pb_history_percentile": "0.1", "pe_industry_ratio": "0.7",
        "pb_industry_ratio": "0.8", "valuation_history_years": "5",
        "dividend_years": "3", "risk_clear": "1", "dividend_yield": "0.04",
        "amount_mean_20": "150000000", "roe_mean_3y": "0.16",
        "net_income_sum_3y": "1000000000", "ocf_profit_3y": "1.1",
        "ocf_ttm": "100000000", "deducted_profit_ttm": "100000000",
        "deducted_profit_trend_clear": "1", "goodwill_equity": "0.02",
        "receivables_quality_clear": "1", "interest_debt_assets": "0.1",
        "unrestricted_cash_short_debt": "2", "controller_pledge": "0.1",
        "pledge_safety_clear": "1", "moat_stable": "1",
        "dividend_coverage_clear": "1", "coverage_percentile": "0.1",
        "institution_percentile": "0.2", "turnover_percentile": "0.3",
    }
    return Candidate(
        symbol=symbol, industry=industry, model="stable_nonfinancial", attribution="B",
        thesis="测试假设，非真实证券", invalidation="现金流恶化则证伪",
        metrics={k: evidence(v) for k, v in values.items()},
        catalysts=[Catalyst(kind="capital_return", evidence=evidence(),
                            verify_by=date(2026, 12, 1), thesis="核对实际注销",
                            invalidation="未执行则失效")],
    )


def evaluate(c):
    return screen(ScreenRequest(as_of=DAY, candidates=[c]))["rows"][0]


def test_screen_has_fixed_explainable_scores_and_no_trading_qualification():
    result = screen(ScreenRequest(as_of=DAY, candidates=[candidate()]))
    row = result["rows"][0]
    assert row["status"] == "selected"
    assert Decimal(80) < row["score"] <= Decimal(100)
    assert sum(row["components"].values()) == row["score"]
    assert result["selected_symbols"] == ["AAA"]
    assert not result["diversification_ready"]
    assert not result["backtest_eligible"]
    assert not result["historical_pit_certified"]
    assert len(result["input_sha256"]) == 64


@pytest.mark.parametrize("change", ["missing", "future", "expired", "untrusted"])
def test_unavailable_safety_evidence_never_passes(change):
    c = candidate()
    if change == "missing":
        del c.metrics["risk_clear"]
    else:
        update = {"future": {"known_on": date(2026, 10, 29)},
                  "expired": {"valid_through": date(2026, 10, 27)},
                  "untrusted": {"trusted": False}}[change]
        c.metrics["risk_clear"] = c.metrics["risk_clear"].model_copy(update=update)
    assert evaluate(c)["status"] == "blocked"


@pytest.mark.parametrize("name,value", [
    ("risk_clear", "0"), ("ocf_profit_3y", "0.79"), ("roe_mean_3y", "0.119"),
    ("goodwill_equity", "0.15"), ("controller_pledge", "0.30"),
    ("net_income_sum_3y", "0"), ("dividend_yield", "0.029"),
])
def test_hard_gates_override_high_scores(name, value):
    c = candidate()
    c.metrics[name] = evidence(value)
    row = evaluate(c)
    assert row["status"] == "excluded"
    assert row["score"] is None
    assert f"failed:{name}" in row["reasons"]


@pytest.mark.parametrize("value", ["-0.1", "1.1"])
def test_percentile_outside_unit_interval_is_blocked(value):
    c = candidate()
    c.metrics["pe_history_percentile"] = evidence(value)
    assert evaluate(c)["status"] == "blocked"


def test_no_catalyst_and_unconfirmed_one_off_are_watch_only():
    c = candidate()
    c.catalysts = []
    assert evaluate(c)["reasons"] == ["no_verified_catalyst"]
    c = candidate().model_copy(update={"attribution": "C"})
    assert evaluate(c)["status"] == "watch"
    c.metrics["one_off_confirmed"] = evidence()
    assert evaluate(c)["status"] == "selected"
    assert evaluate(candidate().model_copy(update={"attribution": "D"}))["status"] == "excluded"


@pytest.mark.parametrize("deadline,expected", [
    (date(2026, 10, 28), "watch"), (date(2027, 4, 29), "selected"),
    (date(2027, 4, 30), "watch"),
])
def test_catalyst_horizon_boundary(deadline, expected):
    c = candidate()
    c.catalysts[0].verify_by = deadline
    assert evaluate(c)["status"] == expected


def test_same_industry_whitespace_cannot_bypass_top_two_and_ties_are_stable():
    candidates = [candidate("C", " 食品 "), candidate("B"), candidate("A")]
    result = screen(ScreenRequest(as_of=DAY, candidates=candidates))
    assert result["selected_symbols"] == ["A", "B"]
    assert result["rows"][2]["reasons"] == ["industry_top_2_only"]
    reverse = screen(ScreenRequest(as_of=DAY, candidates=list(reversed(candidates))))
    assert result["rows"] == reverse["rows"]


def test_max_eight_pause_and_diversification():
    candidates = [candidate(f"S{i}", f"行业{i // 2}") for i in range(10)]
    result = screen(ScreenRequest(as_of=DAY, candidates=candidates))
    assert len(result["selected_symbols"]) == 8
    assert result["diversification_ready"]
    paused = screen(ScreenRequest(as_of=DAY, candidates=candidates, pause_new_entries=True))
    assert paused["selected_symbols"] == []
    assert all(r["reasons"] == ["new_entries_paused"] for r in paused["rows"])


def test_unknown_attention_and_unsupported_models_fail_closed():
    c = candidate()
    del c.metrics["coverage_percentile"]
    assert evaluate(c)["status"] == "blocked"
    for model in ("financial", "cyclical"):
        assert evaluate(candidate().model_copy(update={"model": model}))["status"] == "blocked"


def test_duplicate_symbols_and_nonfinite_values_rejected():
    with pytest.raises(ValidationError, match="unique"):
        ScreenRequest(as_of=DAY, candidates=[candidate("a"), candidate(" A ")])
    with pytest.raises(ValidationError):
        evidence("NaN")


def test_empty_universe_does_not_relax_rules():
    result = screen(ScreenRequest(as_of=DAY, candidates=[]))
    assert result["selected_symbols"] == []
    assert not result["diversification_ready"]


def test_known_veto_is_excluded_even_with_missing_same_stage_data():
    c = candidate()
    c.metrics["risk_clear"] = evidence("0")
    del c.metrics["market_cap"]
    row = evaluate(c)
    assert row["status"] == "excluded"
    assert "failed:risk_clear" in row["reasons"]
    assert "missing_or_unusable:market_cap" in row["reasons"]


def test_passing_gates_does_not_guarantee_minimum_score():
    c = candidate()
    for name, value in {
        "pe_history_percentile": "0.2", "pb_history_percentile": "0.2",
        "roe_mean_3y": "0.12", "ocf_profit_3y": "0.8",
        "goodwill_equity": "0.149", "interest_debt_assets": "0.399",
        "unrestricted_cash_short_debt": "1", "dividend_yield": "0.03",
    }.items():
        c.metrics[name] = evidence(value)
    row = evaluate(c)
    assert row["status"] == "watch"
    assert row["score"] < 70
    assert row["reasons"] == ["score_below_70"]


def test_cli_example_end_to_end():
    root = Path(__file__).resolve().parents[1]
    output = subprocess.run(
        [sys.executable, "-m", "scripts.research.screen_quality_value_attention",
         "scripts/research/examples/quality_value_attention.json"],
        cwd=root, capture_output=True, check=True,
    )
    result = json.loads(output.stdout)
    assert result["selected_symbols"] == ["DEMO_PASS"]
    assert {row["status"] for row in result["rows"]} == {
        "selected", "watch", "excluded", "blocked",
    }
