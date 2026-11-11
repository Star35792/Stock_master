from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.price_value_diagnostics import diagnose

SYMBOL = "600001.SH"
AS_OF = date(2026, 10, 28)
SESSIONS = [AS_OF - timedelta(days=offset) for offset in range(20, -1, -1)]


def _prices(values=None):
    values = values or [Decimal(10) + Decimal(i) / 10 for i in range(21)]
    return [{
        "symbol": SYMBOL, "trade_date": day, "close": value, "adjust_type": "qfq",
        "source_name": "fixture", "quality_status": "trusted", "is_suspect": False,
        "is_suspended": False,
    } for day, value in zip(SESSIONS, values, strict=True)]


def _financials():
    rows = []
    for year in (2023, 2024, 2025):
        for kind in ("income", "cash_flow"):
            rows.append({
                "id": f"{kind}-{year}", "symbol": SYMBOL, "statement_type": kind,
                "period_end": date(year, 12, 31), "report_date": date(year + 1, 4, 1),
                "created_at": datetime(2026, 10, 1, tzinfo=UTC),
                "updated_at": datetime(2026, 10, 1, tzinfo=UTC),
                "currency": "CNY", "source_name": "fixture",
                "metrics": {"net_profit" if kind == "income" else "act_cash_flow_net": "100"},
            })
    for year, amount in ((2025, "40"), (2026, "60")):
        rows.append({
            "id": f"cash-half-{year}", "symbol": SYMBOL, "statement_type": "cash_flow",
            "period_end": date(year, 6, 30), "report_date": date(year, 8, 1),
            "created_at": datetime(2026, 10, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 10, 1, tzinfo=UTC),
            "currency": "CNY", "source_name": "fixture",
            "metrics": {"act_cash_flow_net": amount},
        })
    return rows


def _run(prices=None, financial_rows=None, sessions=None, **extra):
    return diagnose(
        symbol=SYMBOL, as_of=AS_OF, sessions=SESSIONS if sessions is None else sessions,
        prices=_prices() if prices is None else prices,
        financial_rows=_financials() if financial_rows is None else financial_rows, **extra,
    )


def test_supported_quality_and_improving_prices_are_research_eligible():
    result = _run()
    assert result["version"] == "value_research_diagnostics_v1"
    assert result["review_eligible"]
    assert result["quality"]["status"] == "supported"
    assert result["quality"]["net_income_sum_3y"] == "300"
    assert result["quality"]["ocf_profit_3y"] == "1"
    assert result["quality"]["ocf_ttm"] == "120"
    assert result["quality"]["net_income_positive_years_3y"] == "3"
    assert result["quality"]["net_income_min_3y"] == "100"
    assert len(result["trend"]["source_refs"]) == 21
    assert result["quality"]["source_refs"]
    assert Decimal(result["trend"]["return_20"]) == Decimal("0.2")


def test_annual_evidence_does_not_change_original_diagnostic_eligibility():
    rows = _financials()
    rows[0]["metrics"] = {"net_profit": "-1"}
    result = _run(financial_rows=rows)
    assert result["quality"]["status"] == "supported"
    assert result["review_eligible"]
    assert result["quality"]["net_income_positive_years_3y"] == "2"
    assert result["quality"]["net_income_min_3y"] == "-1"


def test_profit_ttm_evidence_uses_actual_availability_without_changing_v1_gate():
    cutoff = datetime(2026, 10, 29, 3, tzinfo=UTC)
    rows = _financials()
    for year, value in ((2025, "80"), (2026, "-50")):
        rows.append({
            **rows[0], "id": f"profit-half-{year}",
            "period_end": date(year, 6, 30), "report_date": date(year, 8, 1),
            "metrics": {"net_profit": value},
        })
    result = _run(financial_rows=rows, financial_as_of=date(2026, 10, 29),
                  financial_available_at=cutoff)
    assert result["quality"]["net_income_ttm"] == "-30"
    assert result["quality"]["status"] == "supported"
    assert result["review_eligible"]
    rows[-1]["updated_at"] = cutoff + timedelta(microseconds=1)
    result = _run(financial_rows=rows, financial_as_of=date(2026, 10, 29),
                  financial_available_at=cutoff)
    assert result["quality"]["net_income_ttm"] is None
    assert result["review_eligible"]


def test_annual_evidence_uses_exact_current_analysis_timestamp_cutoff():
    cutoff = datetime(2026, 10, 29, 3, tzinfo=UTC)
    rows = _financials()
    rows[0]["updated_at"] = cutoff + timedelta(microseconds=1)
    result = _run(financial_rows=rows, financial_as_of=date(2026, 10, 29),
                  financial_available_at=cutoff)
    assert result["quality"]["net_income_positive_years_3y"] is None
    assert result["quality"]["net_income_min_3y"] is None
    rows[0]["updated_at"] = cutoff
    result = _run(financial_rows=rows, financial_as_of=date(2026, 10, 29),
                  financial_available_at=cutoff)
    assert result["quality"]["net_income_positive_years_3y"] == "3"
    assert result["quality"]["net_income_min_3y"] == "100"


def test_falling_prices_remain_weak_even_when_quality_is_supported():
    result = _run(prices=_prices(list(reversed([Decimal(10) + Decimal(i) for i in range(21)]))))
    assert result["trend"]["status"] == "weak"
    assert result["quality"]["status"] == "supported"
    assert not result["review_eligible"]


def test_flat_prices_and_conflicting_trend_are_mixed():
    assert _run(prices=_prices([Decimal(10)] * 21))["trend"]["status"] == "mixed"
    values = [Decimal(10)] + [Decimal(30)] * 19 + [Decimal(20)]
    result = _run(prices=_prices(values))
    assert result["trend"]["status"] == "mixed"
    assert not result["review_eligible"]


@pytest.mark.parametrize("change", [
    {"close": "NaN"}, {"close": "Infinity"}, {"close": "0"}, {"close": -1},
    {"close": 1.0}, {"close": True}, {"source_name": ""}, {"is_suspect": True},
    {"is_suspect": None}, {"is_suspended": True}, {"quality_status": "suspect"},
    {"adjust_type": "none"}, {"adjust_type": None}, {"symbol": "600002.SH"},
])
def test_untrusted_or_unadjusted_observation_cannot_pass(change):
    prices = _prices()
    prices[10].update(change)
    result = _run(prices=prices)
    assert result["trend"]["status"] == "unknown"
    assert result["trend"]["return_20"] is None
    assert not result["review_eligible"]


def test_exact_session_window_rejects_missing_duplicate_future_and_stale_rows():
    for prices in (_prices()[:-1], _prices() + [_prices()[0]], []):
        assert _run(prices=prices)["trend"]["status"] == "unknown"
    for changed_day in (SESSIONS[0], AS_OF + timedelta(days=1), SESSIONS[0] - timedelta(days=1)):
        prices = _prices()
        prices[-1]["trade_date"] = changed_day
        assert _run(prices=prices)["trend"]["status"] == "unknown"
    assert _run(sessions=SESSIONS[:-1])["trend"]["status"] == "unknown"
    assert _run(sessions=SESSIONS[:-1] + [SESSIONS[0]])["trend"]["status"] == "unknown"
    assert _run(sessions=[day - timedelta(days=1) for day in SESSIONS])[
        "trend"]["status"] == "unknown"


def test_order_does_not_change_diagnostics_or_mutate_inputs():
    prices = _prices()
    before = deepcopy(prices)
    assert _run(prices=prices) == _run(
        prices=list(reversed(prices)), sessions=list(reversed(SESSIONS)),
    )
    assert prices == before


def test_missing_financials_and_financial_sector_do_not_pass():
    assert _run(financial_rows=[])["quality"]["status"] == "unknown"
    result = _run(financial_sector=True)
    assert result["quality"]["status"] == "not_applicable"
    assert not result["review_eligible"]


@pytest.mark.parametrize("field,value", [
    ("report_date", AS_OF + timedelta(days=1)),
    ("created_at", datetime(2026, 10, 29, tzinfo=UTC)),
    ("updated_at", datetime(2026, 10, 29, tzinfo=UTC)),
    ("updated_at", datetime(2026, 10, 1)),
    ("currency", "USD"),
])
def test_future_or_unverifiable_financial_data_stays_unknown(field, value):
    rows = _financials()
    rows[0][field] = value
    result = _run(financial_rows=rows)
    assert result["quality"]["status"] == "unknown"
    assert not result["review_eligible"]


def test_conflicting_financials_and_symbol_mismatch_cannot_pass():
    rows = _financials()
    conflict = deepcopy(rows[0])
    conflict["id"] = "conflict"
    conflict["metrics"] = {"net_profit": "101"}
    assert _run(financial_rows=rows + [conflict])["quality"]["status"] == "unknown"
    rows[0]["symbol"] = "600002.SH"
    assert _run(financial_rows=rows)["quality"]["reason_codes"] == ["quality_symbol_mismatch"]


def test_known_quality_failure_is_risk_even_with_another_missing_metric():
    rows = _financials()
    for row in rows:
        if row["statement_type"] == "income":
            row["metrics"]["net_profit"] = "-100"
    result = _run(financial_rows=rows)
    assert result["quality"]["status"] == "risk"
    assert "quality_net_income_sum_3y_insufficient" in result["quality"]["reason_codes"]
    assert not result["review_eligible"]


def test_cash_conversion_below_one_and_negative_ttm_are_quality_risks():
    rows = _financials()
    rows[1]["metrics"]["act_cash_flow_net"] = "99"
    assert _run(financial_rows=rows)["quality"]["status"] == "risk"
    rows = _financials()
    rows[-1]["metrics"]["act_cash_flow_net"] = "-100"
    assert "quality_ocf_ttm_insufficient" in _run(financial_rows=rows)[
        "quality"]["reason_codes"]


def test_current_financial_date_does_not_change_price_window_or_default_policy():
    rows = _financials()
    cutoff = datetime(2026, 10, 29, 8, tzinfo=UTC)
    for row in rows:
        row.update(created_at=cutoff, updated_at=cutoff)
    before = deepcopy(rows)
    assert _run(financial_rows=rows)["quality"]["status"] == "unknown"
    result = _run(
        financial_rows=rows, financial_as_of=date(2026, 10, 29),
        financial_available_at=cutoff,
    )
    assert result["review_eligible"]
    assert result["trend"] == _run()["trend"]
    assert rows == before


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_exact_financial_cutoff_rejects_same_day_later_observation(field):
    rows = _financials()
    cutoff = datetime(2026, 10, 28, 8, tzinfo=UTC)
    rows[0][field] = cutoff + timedelta(microseconds=1)
    # Existing date-only behavior remains backward compatible.
    assert _run(financial_rows=rows)["review_eligible"]
    result = _run(financial_rows=rows, financial_available_at=cutoff)
    assert result["quality"]["status"] == "unknown"
    assert not result["review_eligible"]
    assert all("income-2023" not in ref for ref in result["quality"]["source_refs"])


@pytest.mark.parametrize("value", [
    "2026-10-28T16:00:00+08:00", "2026-10-28T08:00:00Z",
    datetime.fromisoformat("2026-10-28T01:00:00-07:00"),
])
def test_financial_cutoff_accepts_equal_instants_in_other_timezones(value):
    rows = _financials()
    rows[0].update(created_at=value, updated_at=value)
    assert _run(financial_rows=rows, financial_available_at=datetime(
        2026, 10, 28, 8, tzinfo=UTC,
    ))["review_eligible"]


@pytest.mark.parametrize("value", [
    None, "invalid", "2026-10-28", "2026-10-28T08:00:00",
    date(2026, 10, 28), datetime(2026, 10, 28, 8),
])
def test_financial_cutoff_requires_verifiable_collection_and_revision_instants(value):
    rows = _financials()
    rows[0]["updated_at"] = value
    assert not _run(financial_rows=rows, financial_available_at=datetime(
        2026, 10, 28, 8, tzinfo=UTC,
    ))["review_eligible"]


def test_financial_date_uses_shanghai_day_at_utc_boundary():
    cutoff = datetime(2026, 10, 28, 16, tzinfo=UTC)  # Shanghai 10/29 00:00.
    rows = _financials()
    rows[0].update(created_at=cutoff, updated_at=cutoff)
    assert _run(
        financial_rows=rows, financial_as_of=date(2026, 10, 29),
        financial_available_at=cutoff,
    )["review_eligible"]
    assert not _run(
        financial_rows=rows, financial_available_at=cutoff,
    )["review_eligible"]


@pytest.mark.parametrize("extra", [
    {"financial_available_at": datetime(2026, 10, 28, 8)},
    {"financial_available_at": date(2026, 10, 28)},
    {"financial_as_of": datetime(2026, 10, 29, tzinfo=UTC)},
    {"financial_as_of": date(2026, 10, 29),
     "financial_available_at": datetime(2026, 10, 28, 8, tzinfo=UTC)},
])
def test_invalid_financial_cutoff_contract_is_rejected(extra):
    with pytest.raises(ValueError):
        _run(**extra)
