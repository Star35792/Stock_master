import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.services.quality_value_financial import financial_metrics

DAY = date(2026, 10, 28)


def row(year, kind, value, month=12, **changes):
    end = date(year, month, 31 if month in {3, 12} else 30)
    data = dict(id=f"{kind}-{end}", symbol="AAA", statement_type=kind,
                period_end=end, report_date=date(year + 1, 4, 1) if month == 12
                else date(year, month + 1, 20), currency="CNY",
                metrics={"net_profit" if kind == "income" else "act_cash_flow_net": value},
                source_name="curated_fixture", created_at=date(2026, 10, 1),
                updated_at=date(2026, 10, 2))
    data.update(changes)
    return data


def annuals():
    return [row(y, kind, "100" if kind == "income" else "120")
            for y in (2023, 2024, 2025) for kind in ("income", "cash_flow")]


def test_exact_three_year_totals_but_old_annual_is_not_october_ttm():
    result = financial_metrics(annuals(), DAY)
    assert result["net_income_sum_3y"].value == Decimal(300)
    assert result["ocf_profit_3y"].value == Decimal("1.2")
    assert "ocf_ttm" not in result
    assert result["ocf_profit_3y"].known_on == date(2026, 10, 2)
    assert result["ocf_profit_3y"].valid_through == DAY
    assert len(json.loads(result["ocf_profit_3y"].source_ref)) == 6
    assert set(result) == {"net_income_sum_3y", "ocf_profit_3y",
                           "net_income_positive_years_3y", "net_income_min_3y"}


def test_cumulative_ttm_and_decimal_json_precision():
    rows = annuals() + [row(2026, "cash_flow", "80", month=6),
                        row(2025, "cash_flow", "50", month=6)]
    rows[-2]["metrics"] = '{"act_cash_flow_net":80.123456789123}'
    assert financial_metrics(rows, DAY)["ocf_ttm"].value == Decimal("150.123456789123")


@pytest.mark.parametrize("latest,expected_positive", [("-1", 2), ("0", 2), ("1", 3)])
def test_annual_profit_continuity_does_not_hide_one_bad_year(latest, expected_positive):
    rows = annuals()
    for item in rows:
        if item["statement_type"] == "income" and item["period_end"].year == 2025:
            item["metrics"] = {"net_profit": latest}
    result = financial_metrics(rows, DAY)
    assert result["net_income_sum_3y"].value > 0
    assert result["net_income_positive_years_3y"].value == expected_positive
    assert result["net_income_min_3y"].value == Decimal(latest)
    assert len(json.loads(result["net_income_min_3y"].source_ref)) == 3


def test_annual_profit_continuity_requires_all_fixed_years_and_exact_values():
    rows = annuals()
    rows[0]["metrics"] = '{"net_profit":0.000000000000123456789}'
    result = financial_metrics(rows, DAY)
    assert result["net_income_positive_years_3y"].value == 3
    assert result["net_income_min_3y"].value == Decimal("0.000000000000123456789")
    for bad_rows in (
        rows[1:],
        [dict(r, updated_at=date(2026, 10, 29)) if r is rows[0] else r for r in rows],
        rows + [dict(rows[0], id="conflict", metrics={"net_profit": "2"})],
    ):
        bad = financial_metrics(bad_rows, DAY)
        assert "net_income_positive_years_3y" not in bad
        assert "net_income_min_3y" not in bad


def test_no_missing_year_backfill_or_incomplete_ttm_fallback():
    rows = [r for r in annuals() if r["period_end"].year != 2024]
    rows += [row(2022, "income", "100"), row(2026, "cash_flow", "10", month=6)]
    assert financial_metrics(rows, DAY) == {}


def test_conflicting_duplicates_block_affected_metric_but_identical_allowed():
    rows = annuals()
    duplicate = dict(rows[0], id="duplicate")
    assert financial_metrics(rows + [duplicate], DAY)["net_income_sum_3y"].value == 300
    duplicate["metrics"] = {"net_profit": "99"}
    result = financial_metrics(rows + [duplicate], DAY)
    assert "net_income_sum_3y" not in result and "ocf_profit_3y" not in result
    assert "ocf_ttm" not in result


def test_observable_annual_can_be_ttm_before_first_quarter_deadline():
    rows = [row(2025, "cash_flow", "120", created_at=date(2026, 4, 1),
                updated_at=date(2026, 4, 2))]
    assert financial_metrics(rows, date(2026, 4, 29))["ocf_ttm"].value == 120
    assert "ocf_ttm" not in financial_metrics(rows, date(2026, 4, 30))
    assert financial_metrics(rows, date(2026, 3, 31)) == {}


@pytest.mark.parametrize("day,latest_month,expected", [
    (date(2026, 4, 30), 3, True),
    (date(2026, 8, 30), 3, True),
    (date(2026, 8, 31), 3, False),
    (date(2026, 8, 31), 6, True),
    (date(2026, 10, 28), 6, True),
    (date(2026, 10, 30), 6, True),
    (date(2026, 10, 31), 6, False),
    (date(2026, 10, 31), 9, True),
])
def test_inclusive_disclosure_cutoffs_require_current_minimum_period(
        day, latest_month, expected):
    rows = [row(2025, "cash_flow", "120"),
            row(2025, "cash_flow", "50", month=latest_month),
            row(2026, "cash_flow", "80", month=latest_month)]
    for r in rows:
        r["created_at"] = r["report_date"]
        r["updated_at"] = r["report_date"]
    result = financial_metrics(rows, day)
    assert ("ocf_ttm" in result) == expected
    if expected:
        assert result["ocf_ttm"].value == 150


def test_newest_cash_period_missing_comparable_or_conflicting_never_falls_back():
    rows = annuals() + [row(2026, "cash_flow", "80", month=6),
                        row(2025, "cash_flow", "50", month=6),
                        row(2026, "cash_flow", "90", month=9)]
    assert "ocf_ttm" not in financial_metrics(rows, DAY)
    rows += [row(2025, "cash_flow", "60", month=9)]
    assert financial_metrics(rows, DAY)["ocf_ttm"].value == 150
    rows += [row(2026, "cash_flow", "91", month=9, id="conflict")]
    assert "ocf_ttm" not in financial_metrics(rows, DAY)


@pytest.mark.parametrize("change", [
    {"currency": "USD"}, {"currency": None},
    {"report_date": date(2026, 10, 29)},
    {"created_at": date(2026, 10, 29)},
    {"updated_at": date(2026, 10, 29)},
    {"created_at": None}, {"updated_at": datetime(2026, 10, 1)},
    {"metrics": {"net_profit": "NaN"}},
])
def test_unusable_rows_never_certify_three_year_profit(change):
    rows = annuals()
    rows[0].update(change)
    assert "net_income_sum_3y" not in financial_metrics(rows, DAY)


def test_collection_timestamp_uses_shanghai_calendar():
    rows = annuals()
    rows[0]["updated_at"] = datetime(2026, 10, 28, 17, tzinfo=UTC)
    assert "net_income_sum_3y" not in financial_metrics(rows, DAY)


def test_nonpositive_profit_no_ratio_and_multiple_symbols_rejected():
    rows = annuals()
    for r in rows:
        if r["statement_type"] == "income":
            r["metrics"] = {"net_profit": "-1"}
    result = financial_metrics(rows, DAY)
    assert result["net_income_sum_3y"].value == -3
    assert "ocf_profit_3y" not in result
    with pytest.raises(ValueError, match="one nonblank symbol"):
        financial_metrics(rows + [dict(rows[0], symbol="BBB")], DAY)


def test_profit_ttm_exact_formula_and_three_contributing_sources():
    rows = annuals() + [row(2026, "income", "60.123456789123", month=6),
                        row(2025, "income", "80", month=6)]
    result = financial_metrics(rows, DAY)["net_income_ttm"]
    assert result.value == Decimal("80.123456789123")
    assert len(json.loads(result.source_ref)) == 3
    assert result.known_on == date(2026, 10, 2)
    assert result.valid_through == DAY


@pytest.mark.parametrize("bad", [None, True, 1.0, "NaN", "Infinity", "bad"])
def test_latest_disclosed_income_missing_profit_never_uses_older_quarter(bad):
    rows = annuals() + [row(2026, "income", "80", month=6),
                        row(2025, "income", "50", month=6),
                        row(2026, "income", bad, month=9),
                        row(2025, "income", "60", month=9)]
    assert "net_income_ttm" not in financial_metrics(rows, DAY)


def test_latest_income_missing_comparable_conflicting_or_future_is_not_certified():
    rows = annuals() + [row(2026, "income", "80", month=6),
                        row(2025, "income", "50", month=6),
                        row(2026, "income", "90", month=9)]
    assert "net_income_ttm" not in financial_metrics(rows, DAY)
    rows.append(row(2025, "income", "60", month=9))
    result = financial_metrics(rows, DAY)["net_income_ttm"]
    assert result.value == 130
    assert any("income-2026-09-30" in ref for ref in json.loads(result.source_ref))
    for change in ({"metrics": {"net_profit": "91"}, "id": "conflict"},
                   {"metrics": {}, "id": "missing-version"}):
        assert "net_income_ttm" not in financial_metrics(rows + [dict(rows[-2], **change)], DAY)
    rows[-2]["updated_at"] = date(2026, 10, 29)
    # Future revision cannot be used; an observed H1 remains available today.
    result = financial_metrics(rows, DAY)["net_income_ttm"]
    assert result.value == 130
    assert all("income-2026-09-30" not in ref for ref in json.loads(result.source_ref))


@pytest.mark.parametrize("day,month,expected", [
    (date(2026, 4, 29), None, True), (date(2026, 4, 30), None, False),
    (date(2026, 4, 30), 3, True), (date(2026, 8, 30), 3, True),
    (date(2026, 8, 31), 3, False), (date(2026, 8, 31), 6, True),
    (date(2026, 10, 30), 6, True), (date(2026, 10, 31), 6, False),
    (date(2026, 10, 31), 9, True),
])
def test_profit_ttm_disclosure_cutoffs(day, month, expected):
    rows = [row(2025, "income", "100")]
    if month:
        rows += [row(2026, "income", "60", month=month),
                 row(2025, "income", "50", month=month)]
    for item in rows:
        item["created_at"] = item["updated_at"] = item["report_date"]
    assert ("net_income_ttm" in financial_metrics(rows, day)) == expected
