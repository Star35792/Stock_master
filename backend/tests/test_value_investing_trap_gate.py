"""Tests for the value-trap gate and the growth path (``value_investing_margin@v3``).

The kernel tests are pure: they build the evidence by hand and assert the
exact ``Decimal`` outcome.  The SQL-semantics tests pin the shared streak
fragments (``app.services.value_trap_trend``) against crafted series, so a
gap in a series can never again be read as a three-year collapse -- the bug
that once inflated the run counts above the market threshold basis.

The point of these tests is the semantics, not the arithmetic:

* a falling business is a *known disqualification* (excluded), not missing
  evidence (blocked);
* the trap gate outranks the growth path, because a business that grows
  revenue while its profit falls and its leverage climbs is a trap with a
  good headline;
* an incomplete trend window blocks rather than silently shortening the
  test.
"""

from decimal import Decimal

import duckdb
import pytest

from app.services.value_trap_trend import (
    DEBT_RATIO_CHANGE_SQL,
    NET_PROFIT_DECLINE_STREAK_SQL,
    REVENUE_DECLINE_STREAK_SQL,
)
from app.strategies.value_investing import (
    ValueInvestingEvidence,
    ValueInvestingParams,
    evaluate,
    quality_profile,
    screen,
)

#: The v3 thresholds, matching ``app.strategies.registry``.
V3_PARAMS = ValueInvestingParams(
    required_margin=Decimal("0.30"),
    min_roe_3y=Decimal("0.08"),
    min_revenue_cagr_3y=Decimal("0.12"),
    min_net_profit_cagr_3y=Decimal("0.15"),
    decline_streak_years=2,
    max_debt_ratio_rise_3y=Decimal("0.10"),
)

#: A healthy business: high return on equity, growing, no decline streak.
HEALTHY_TREND = dict(
    revenue_cagr_3y=Decimal("0.20"),
    net_profit_cagr_3y=Decimal("0.18"),
    revenue_decline_streak=0,
    net_profit_decline_streak=0,
    debt_ratio_change_3y=Decimal("0.02"),
    trend_periods_present=4,
)


def _evidence(**overrides) -> ValueInvestingEvidence:
    """Build a security that passes every gate of the strategy."""
    base = dict(
        symbol="600000.SH",
        name="测试股份",
        industry="通用设备",
        close=Decimal("3.00"),
        total_shares=Decimal("100000000"),
        ttm_net_profit=Decimal("100000000"),
        ttm_period_end=None,
        net_profit_3y=Decimal("300000000"),
        ocf_3y=Decimal("300000000"),
        equity=Decimal("500000000"),
        equity_period_end=None,
        pe_history=tuple(Decimal("10") for _ in range(600)),
        pb_history=tuple(Decimal("1") for _ in range(600)),
        vendor_pe_ttm=Decimal("3"),
        vendor_pb_mrq=Decimal("0.6"),
        listed_days=2000,
        amount_20d=Decimal("80000000"),
        is_suspended=False,
        special_treatment_status="normal",
        is_financial_sector=False,
        is_cyclic_industry=False,
        roe_3y=Decimal("0.20"),
        annual_periods_present=3,
        equity_periods_present=3,
    )
    base.update(HEALTHY_TREND)
    base.update(overrides)
    return ValueInvestingEvidence(**base)


# ---------------------------------------------------------- value-trap gate


def test_a_business_that_keeps_falling_is_excluded_not_blocked():
    """A falling business is known-bad; it is not missing evidence."""
    result = evaluate(
        _evidence(net_profit_decline_streak=2), V3_PARAMS
    )
    assert result["status"] == "excluded"
    assert "net_profit_declining" in result["excluded_reasons"]
    assert "net_profit_declining" not in result["blocking_reasons"]


def test_a_shrinking_revenue_is_a_trap():
    result = evaluate(_evidence(revenue_decline_streak=3), V3_PARAMS)
    assert result["status"] == "excluded"
    assert "revenue_declining" in result["excluded_reasons"]


def test_a_single_down_year_is_not_yet_a_trap():
    """One bad year is noise; two in a row is a direction."""
    result = evaluate(
        _evidence(revenue_decline_streak=1, net_profit_decline_streak=1),
        V3_PARAMS,
    )
    assert result["status"] == "qualified"


def test_levering_up_is_a_trap():
    result = evaluate(
        _evidence(debt_ratio_change_3y=Decimal("0.15")), V3_PARAMS
    )
    assert result["status"] == "excluded"
    assert "leverage_rising" in result["excluded_reasons"]


def test_leverage_at_the_limit_is_still_allowed():
    """The threshold is exclusive: exactly at the cap passes."""
    result = evaluate(
        _evidence(debt_ratio_change_3y=Decimal("0.10")), V3_PARAMS
    )
    assert "leverage_rising" not in result["reason_codes"]


def test_improving_leverage_is_not_a_trap():
    result = evaluate(
        _evidence(debt_ratio_change_3y=Decimal("-0.08")), V3_PARAMS
    )
    assert "leverage_rising" not in result["reason_codes"]


def test_an_incomplete_trend_window_blocks_the_row():
    """A partial window would turn a three-year test into a one-year one."""
    result = evaluate(_evidence(trend_periods_present=3), V3_PARAMS)
    assert result["status"] == "blocked"
    assert "trend_window_incomplete" in result["blocking_reasons"]


def test_the_trap_gate_is_off_when_no_threshold_is_set():
    params = ValueInvestingParams(required_margin=Decimal("0.30"))
    result = evaluate(
        _evidence(net_profit_decline_streak=3, debt_ratio_change_3y=Decimal("0.5")),
        params,
    )
    assert "net_profit_declining" not in result["reason_codes"]
    assert "leverage_rising" not in result["reason_codes"]


# -------------------------------------------------------------- growth path


def test_the_growth_path_admits_a_business_with_a_low_return_on_equity():
    """Value and growth are not opposites.

    A business that compounds fast has not yet earned a high return on the
    equity it has accumulated; demanding a mature return on equity would
    reject exactly the businesses worth owning.
    """
    result = evaluate(
        _evidence(roe_3y=Decimal("0.05")), V3_PARAMS
    )
    assert result["status"] == "qualified"
    assert result["metrics"]["growth_path"] == "qualified"
    assert "roe_3y_below_threshold" not in result["reason_codes"]


def test_the_growth_path_needs_both_revenue_and_profit_to_compound():
    result = evaluate(
        _evidence(
            roe_3y=Decimal("0.05"),
            revenue_cagr_3y=Decimal("0.30"),
            net_profit_cagr_3y=Decimal("0.05"),
        ),
        V3_PARAMS,
    )
    assert result["status"] == "excluded"
    assert "roe_3y_below_threshold" in result["excluded_reasons"]
    assert result["metrics"]["growth_path"] == "not_qualified"


def test_the_growth_path_still_requires_the_price_gate():
    """Admitting a growing business must not make it buyable at any price."""
    result = evaluate(
        _evidence(roe_3y=Decimal("0.05"), close=Decimal("30")), V3_PARAMS
    )
    assert result["status"] == "excluded"
    assert "margin_of_safety_not_met" in result["excluded_reasons"]


def test_the_trap_gate_outranks_the_growth_path():
    """Growing revenue while profit falls and leverage climbs is a trap."""
    result = evaluate(
        _evidence(
            roe_3y=Decimal("0.05"),
            revenue_cagr_3y=Decimal("0.30"),
            net_profit_cagr_3y=Decimal("0.30"),
            net_profit_decline_streak=2,
        ),
        V3_PARAMS,
    )
    assert result["status"] == "excluded"
    assert "net_profit_declining" in result["excluded_reasons"]


def test_the_growth_path_is_unavailable_without_a_positive_base():
    """A company that only just turned profitable has no growth rate yet."""
    result = evaluate(
        _evidence(roe_3y=Decimal("0.05"), net_profit_cagr_3y=None),
        V3_PARAMS,
    )
    assert result["metrics"]["growth_path"] == "unavailable"
    assert result["status"] == "excluded"


def test_the_growth_path_is_off_when_no_threshold_is_set():
    params = ValueInvestingParams(
        required_margin=Decimal("0.30"), min_roe_3y=Decimal("0.08")
    )
    result = evaluate(_evidence(roe_3y=Decimal("0.05")), params)
    assert "growth_path" not in result["metrics"]
    assert "roe_3y_below_threshold" in result["excluded_reasons"]


# ------------------------------------------------------------- the report


def test_the_report_counts_the_growth_path_and_the_trap_gate():
    good = _evidence(symbol="600001.SH")
    grower = _evidence(symbol="600002.SH", roe_3y=Decimal("0.05"))
    trap = _evidence(symbol="600003.SH", net_profit_decline_streak=2)
    result = screen([good, grower, trap], V3_PARAMS)
    assert result["growth_path_count"] == 1
    assert result["value_trap_removed_count"] == 1
    assert result["value_trap_removed_symbols"] == ["600003.SH"]


def test_the_trap_gate_survives_the_price_only_comparison():
    """A trap is a trap under every version of the strategy.

    The price-only comparison strips the return-on-equity gate so the cost
    of the quality gate is visible.  It must not strip the trap gate: that
    would present a falling business as a candidate.
    """
    trap = _evidence(symbol="600003.SH", net_profit_decline_streak=2)
    result = screen([trap], V3_PARAMS)
    price_only = result["price_gate_only"]
    assert "600003.SH" not in price_only["qualified_symbols"]


def test_the_quality_profile_declares_the_trap_dimension():
    profile = quality_profile(V3_PARAMS)
    keys = [dimension["key"] for dimension in profile["dimensions"]]
    assert "value_trap_avoidance" in keys
    assert profile["value_trap_gate_enabled"] is True
    assert profile["growth_path_enabled"] is True


def test_the_trap_thresholds_must_be_sane():
    with pytest.raises(ValueError):
        ValueInvestingParams(
            required_margin=Decimal("0.30"), decline_streak_years=0
        )
    with pytest.raises(ValueError):
        ValueInvestingParams(
            required_margin=Decimal("0.30"), max_debt_ratio_rise_3y=Decimal("-0.1")
        )
    with pytest.raises(ValueError):
        ValueInvestingParams(
            required_margin=Decimal("0.30"), min_revenue_cagr_3y=Decimal("0")
        )
    with pytest.raises(ValueError):
        ValueInvestingParams(
            required_margin=Decimal("0.30"), min_net_profit_cagr_3y=Decimal("-1")
        )


# ------------------------------------------------------- SQL streak semantics


def _streaks(series):
    """Run the shared streak fragments over crafted four-point series.

    ``series`` maps a label to ``(base, p1, p2, last)`` year-end values
    (year-4 .. year-1, None for a missing period).
    """
    values = ", ".join(
        f"('{label}', {b!r}, {p1!r}, {p2!r}, {last!r})".replace("None", "NULL")
        for label, (b, p1, p2, last) in sorted(series.items())
    )
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            f"""
            WITH ar(symbol, rev_base, rev_p1, rev_p2, rev_last) AS (VALUES {values}),
                 ap(symbol, np_base, np_p1, np_p2, np_last) AS (VALUES {values})
            SELECT ar.symbol,
                   {REVENUE_DECLINE_STREAK_SQL} AS revenue_streak,
                   {NET_PROFIT_DECLINE_STREAK_SQL} AS profit_streak
            FROM ar JOIN ap ON ap.symbol = ar.symbol
            ORDER BY ar.symbol
            """
        ).fetchall()
    finally:
        connection.close()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_the_streak_sql_counts_only_observed_consecutive_declines():
    streaks = _streaks(
        {
            "full_decline": (10.0, 8.0, 6.0, 4.0),
            "latest_up": (10.0, 8.0, 6.0, 9.0),
            "flat_latest": (10.0, 8.0, 6.0, 6.0),
            "single_decline": (10.0, 4.0, 8.0, 6.0),
        }
    )
    assert streaks["full_decline"] == (3, 3)
    assert streaks["latest_up"] == (0, 0)
    assert streaks["flat_latest"] == (0, 0)
    assert streaks["single_decline"] == (1, 1)


def test_a_missing_period_stops_the_streak_instead_of_inflating_it():
    """A gap is not a decline: missing years must not read as streak=3."""
    streaks = _streaks(
        {
            "missing_middle": (10.0, None, 6.0, 4.0),
            "missing_base": (None, 8.0, 6.0, 4.0),
            "missing_p1_p2": (None, None, 6.0, 4.0),
            "only_latest": (None, None, None, 4.0),
        }
    )
    assert streaks["missing_middle"] == (1, 1)
    assert streaks["missing_base"] == (2, 2)
    assert streaks["missing_p1_p2"] == (1, 1)
    assert streaks["only_latest"] == (None, None)


def test_the_debt_ratio_change_sql_needs_both_endpoints():
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            f"""
            WITH ab(symbol, assets_base, equity_base, assets_last, equity_last)
                 AS (VALUES
                   ('normal', 200.0, 100.0, 100.0, 30.0),
                   ('missing_equity', 200.0, 100.0, 100.0, NULL),
                   ('zero_assets', 0.0, 100.0, 100.0, 30.0)
                 )
            SELECT symbol, {DEBT_RATIO_CHANGE_SQL} AS debt_change
            FROM ab
            """
        ).fetchall()
    finally:
        connection.close()
    by_symbol = {row[0]: row[1] for row in rows}
    assert by_symbol["normal"] == pytest.approx(0.2)
    assert by_symbol["missing_equity"] is None
    assert by_symbol["zero_assets"] is None
