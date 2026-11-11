"""Tests for the built-in value-investing screen and its definition catalog.

The kernel tests are pure: they build the evidence by hand and assert the
exact ``Decimal`` outcome, following the style of
``tests/test_strategy_templates.py``.  The catalog tests use their own
in-memory DuckDB so they never touch the developer's control plane.
"""

from decimal import Decimal

import duckdb
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import SCHEMAS
from app.models.strategy import StrategyDefinition
from app.repositories.strategy import StrategyDefinitionRepository
from app.strategies.registry import (
    BUILTIN_STRATEGY_DEFINITIONS,
    VALUE_INVESTING_NAME,
    VALUE_INVESTING_REQUIRED_MARGIN,
    VALUE_INVESTING_V1_VERSION,
    VALUE_INVESTING_V2_VERSION,
    VALUE_INVESTING_VERSION,
    params_from_definition,
    register_all_strategy_definitions,
)
from app.strategies.value_investing import (
    KERNEL_VERSION,
    ValueInvestingEvidence,
    ValueInvestingParams,
    conservative_quantile,
    evaluate,
    quality_profile,
    screen,
)

PARAMS = ValueInvestingParams(required_margin=Decimal("0.30"))


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
    )
    base.update(overrides)
    return ValueInvestingEvidence(**base)


# --------------------------------------------------------------- parameters


def test_required_margin_must_stay_inside_the_open_unit():
    with pytest.raises(ValueError):
        ValueInvestingParams(required_margin=Decimal("0"))
    with pytest.raises(ValueError):
        ValueInvestingParams(required_margin=Decimal("1"))
    with pytest.raises(ValueError):
        ValueInvestingParams(required_margin=Decimal("1.4"))


def test_price_factor_is_the_complement_of_the_required_margin():
    assert PARAMS.price_factor == Decimal("0.70")


# ----------------------------------------------------------------- quantile


def test_conservative_quantile_returns_an_observed_value():
    values = [Decimal(n) for n in range(1, 101)]
    anchor = conservative_quantile(values, Decimal("0.25"))
    assert anchor == Decimal("25")
    assert anchor in values


def test_conservative_quantile_never_exceeds_the_interpolated_quantile():
    values = [Decimal("10"), Decimal("12"), Decimal("14"), Decimal("20")]
    anchor = conservative_quantile(values, Decimal("0.25"))
    assert anchor == Decimal("10")


def test_conservative_quantile_matches_duckdb_discrete_quantile():
    """The service computes the anchor in SQL; the two paths must agree.

    The comparison goes through a ``DECIMAL`` column on purpose: the
    valuation history is stored as a fixed-point column, and a ``DOUBLE``
    quantile would quietly round the anchor away from the exact value the
    kernel computes.
    """
    values = [
        (Decimal(n) / 7).quantize(Decimal("0.000000000001"))
        for n in range(1, 597)
    ]
    connection = duckdb.connect()
    try:
        connection.execute("CREATE TABLE observations (value DECIMAL(38, 12))")
        connection.execute(
            "INSERT INTO observations SELECT UNNEST(?)",
            [[value for value in values]],
        )
        rows = connection.execute(
            "SELECT quantile_disc(value, 0.25) FROM observations"
        ).fetchone()
    finally:
        connection.close()
    sql_anchor = Decimal(str(rows[0]))
    python_anchor = conservative_quantile(values, Decimal("0.25"))
    assert sql_anchor == python_anchor


def test_conservative_quantile_of_an_empty_series_is_none():
    assert conservative_quantile([], Decimal("0.25")) is None


# -------------------------------------------------------------------- gates


def test_a_clean_security_qualifies():
    result = evaluate(_evidence(), PARAMS)
    assert result["status"] == "qualified"
    assert result["price_condition_met"] is True
    assert result["valuation"]["v_low"] == Decimal("5")
    assert result["valuation"]["buy_cost_ceiling"] == Decimal("3.50")
    assert result["valuation"]["gross_margin"] == Decimal("0.4")
    assert result["reason_codes"] == []


def test_missing_price_blocks_the_row():
    result = evaluate(_evidence(close=None), PARAMS)
    assert result["status"] == "blocked"
    assert "trusted_quote_missing" in result["blocking_reasons"]


def test_unknown_special_treatment_blocks_the_row():
    result = evaluate(_evidence(special_treatment_status=None), PARAMS)
    assert result["status"] == "blocked"
    assert "special_treatment_unknown" in result["blocking_reasons"]


def test_special_treatment_restricted_excludes_the_row():
    result = evaluate(_evidence(special_treatment_status="st"), PARAMS)
    assert result["status"] == "excluded"
    assert "special_treatment_restricted" in result["excluded_reasons"]


def test_financial_sector_is_excluded():
    result = evaluate(_evidence(is_financial_sector=True), PARAMS)
    assert result["status"] == "excluded"
    assert "financial_industry_separate_model" in result["excluded_reasons"]


def test_cyclic_industry_is_excluded():
    result = evaluate(_evidence(is_cyclic_industry=True), PARAMS)
    assert result["status"] == "excluded"
    assert "cyclic_industry_separate_model" in result["excluded_reasons"]


def test_three_year_loss_excludes_the_row():
    result = evaluate(_evidence(net_profit_3y=Decimal("-1")), PARAMS)
    assert result["status"] == "excluded"
    assert "three_year_net_profit_not_positive" in result["excluded_reasons"]


def test_weak_cash_conversion_excludes_the_row():
    result = evaluate(_evidence(ocf_3y=Decimal("100000000")), PARAMS)
    assert result["status"] == "excluded"
    assert "cash_conversion_weak" in result["excluded_reasons"]


def test_loss_making_ttm_excludes_the_row():
    result = evaluate(_evidence(ttm_net_profit=Decimal("-1")), PARAMS)
    assert result["status"] == "excluded"
    assert "ttm_net_profit_not_positive" in result["excluded_reasons"]


def test_thin_valuation_history_blocks_the_row():
    result = evaluate(
        _evidence(pe_history=tuple(Decimal("10") for _ in range(10))),
        PARAMS,
    )
    assert result["status"] == "blocked"
    assert "pe_history_insufficient" in result["blocking_reasons"]


def test_illiquid_security_is_excluded():
    result = evaluate(_evidence(amount_20d=Decimal("1000")), PARAMS)
    assert result["status"] == "excluded"
    assert "liquidity_insufficient" in result["excluded_reasons"]


def test_short_listing_history_is_excluded():
    result = evaluate(_evidence(listed_days=10), PARAMS)
    assert result["status"] == "excluded"
    assert "listed_days_insufficient" in result["excluded_reasons"]


# ------------------------------------------------------------ margin of safety


def test_price_above_the_buy_cost_ceiling_fails_the_margin_gate():
    result = evaluate(_evidence(close=Decimal("3.51")), PARAMS)
    assert result["status"] == "excluded"
    assert "margin_of_safety_not_met" in result["excluded_reasons"]
    assert result["price_condition_met"] is False


def test_price_at_the_buy_cost_ceiling_still_passes():
    """The condition is ``P <= ceiling``; the boundary is inclusive."""
    result = evaluate(_evidence(close=Decimal("3.50")), PARAMS)
    assert result["status"] == "qualified"
    assert result["price_condition_met"] is True


def test_a_larger_margin_tightens_the_ceiling():
    loose = evaluate(_evidence(close=Decimal("4.00")), PARAMS)
    assert loose["status"] == "excluded"
    tight = evaluate(
        _evidence(close=Decimal("4.00")),
        ValueInvestingParams(required_margin=Decimal("0.50")),
    )
    assert tight["valuation"]["buy_cost_ceiling"] == Decimal("2.50")


def test_the_smaller_anchor_binds_the_intrinsic_value():
    """A book anchor below the earnings anchor decides the value."""
    result = evaluate(_evidence(), PARAMS)
    assert result["valuation"]["v_earnings"] == Decimal("10")
    assert result["valuation"]["v_book"] == Decimal("5")
    assert result["valuation"]["v_low"] == Decimal("5")


def test_the_earnings_anchor_binds_when_the_book_anchor_is_higher():
    result = evaluate(_evidence(equity=Decimal("2000000000")), PARAMS)
    assert result["valuation"]["v_book"] == Decimal("20")
    assert result["valuation"]["v_earnings"] == Decimal("10")
    assert result["valuation"]["v_low"] == Decimal("10")


def test_the_anchor_uses_the_supplied_quantile():
    result = evaluate(_evidence(), PARAMS)
    assert result["valuation"]["anchor_quantile"] == Decimal("0.25")
    assert result["valuation"]["pe_fair"] == Decimal("10")


def test_the_current_price_drives_the_current_multiples():
    result = evaluate(_evidence(close=Decimal("2.00")), PARAMS)
    assert result["metrics"]["pe_current"] == Decimal("2")
    assert result["metrics"]["pb_current"] == Decimal("0.4")


# ------------------------------------------------------------------ shortlist


def _many(count: int, *, industry: str, price: str) -> list[ValueInvestingEvidence]:
    return [
        _evidence(
            symbol=f"60{index:04d}.SH",
            name=f"标的{index}",
            industry=industry,
            close=Decimal(price),
        )
        for index in range(count)
    ]


def test_the_shortlist_caps_the_total_number_of_names():
    evidence = [
        _evidence(symbol=f"60{index:04d}.SH", industry=f"行业{index}")
        for index in range(12)
    ]
    result = screen(evidence, PARAMS)
    assert result["qualified_count"] == 12
    assert result["shortlist_count"] == PARAMS.top_n


def test_the_shortlist_caps_the_number_of_names_per_industry():
    evidence = _many(4, industry="通用设备", price="3.00")
    result = screen(evidence, PARAMS)
    assert result["shortlist_count"] == PARAMS.max_per_industry
    assert result["deferred_count"] == 4 - PARAMS.max_per_industry


def test_deferred_names_keep_their_qualified_membership():
    evidence = _many(4, industry="通用设备", price="3.00")
    result = screen(evidence, PARAMS)
    assert all(row["status"] == "qualified_deferred" for row in result["deferred"])
    assert all(row["symbol"] in result["qualified_symbols"] for row in result["deferred"])


def test_the_shortlist_is_ordered_by_the_discount_to_the_conservative_value():
    cheap = _evidence(symbol="600001.SH", close=Decimal("1.00"))
    dear = _evidence(symbol="600002.SH", close=Decimal("3.40"))
    result = screen([dear, cheap], PARAMS)
    assert [row["symbol"] for row in result["shortlist"]] == [
        "600001.SH",
        "600002.SH",
    ]


def test_the_shortlist_is_deterministic_for_equal_discounts():
    evidence = [
        _evidence(symbol="600002.SH"),
        _evidence(symbol="600001.SH"),
    ]
    first = screen(evidence, PARAMS)
    second = screen(list(reversed(evidence)), PARAMS)
    assert [row["symbol"] for row in first["shortlist"]] == [
        row["symbol"] for row in second["shortlist"]
    ]


def test_an_empty_pool_is_not_padded():
    result = screen([], PARAMS)
    assert result["qualified_count"] == 0
    assert result["shortlist_count"] == 0
    assert result["research_only"] is True
    assert result["execution_eligible"] is False


# ------------------------------------------------------------------- catalog


def test_the_catalog_locks_the_margin_of_safety_at_thirty_percent():
    assert VALUE_INVESTING_REQUIRED_MARGIN == Decimal("0.30")
    definitions = {
        item["name"]: item for item in BUILTIN_STRATEGY_DEFINITIONS
    }
    entry = definitions[VALUE_INVESTING_NAME]
    assert entry["required_margin"] == Decimal("0.30")
    assert "required_margin" in entry["locked_parameters"]


def test_the_catalog_declares_the_value_investing_strategy():
    names = {item["name"] for item in BUILTIN_STRATEGY_DEFINITIONS}
    assert VALUE_INVESTING_NAME in names


def _session():
    engine = create_engine("duckdb:///:memory:")
    with engine.connect() as connection:
        for schema in SCHEMAS:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        connection.commit()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_registering_the_definitions_is_idempotent():
    db = _session()
    try:
        register_all_strategy_definitions(db)
        register_all_strategy_definitions(db)
        db.commit()
        items, total = StrategyDefinitionRepository().list(db)
        assert total == len(BUILTIN_STRATEGY_DEFINITIONS)
        assert len(items) == len(BUILTIN_STRATEGY_DEFINITIONS)
    finally:
        db.close()


def test_the_registered_definition_carries_the_locked_margin():
    db = _session()
    try:
        register_all_strategy_definitions(db)
        db.commit()
        definition = StrategyDefinitionRepository().get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_VERSION
        )
        assert definition is not None
        assert definition.required_margin == Decimal("0.3000000000")
        assert "required_margin" in definition.locked_parameters
        assert definition.status == "active"
        assert definition.is_active is True
    finally:
        db.close()


def test_the_parameters_are_rebuilt_from_the_registered_definition():
    db = _session()
    try:
        register_all_strategy_definitions(db)
        db.commit()
        definition = StrategyDefinitionRepository().get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_VERSION
        )
        params = params_from_definition(definition)
        assert params.required_margin == Decimal("0.3000000000")
        assert params.price_factor == Decimal("0.7000000000")
    finally:
        db.close()


def test_a_definition_without_a_margin_is_rejected():
    db = _session()
    try:
        definition = StrategyDefinition(
            name=VALUE_INVESTING_NAME,
            version="v1",
            category="value_investing",
            parameters_schema={},
            locked_parameters=[],
            inputs=[],
        )
        with pytest.raises(ValueError):
            params_from_definition(definition)
    finally:
        db.close()


# --------------------------------------------------- business-quality gate

QUALITY_PARAMS = ValueInvestingParams(
    required_margin=Decimal("0.30"),
    min_roe_3y=Decimal("0.08"),
)


def test_without_a_roe_threshold_the_price_gate_alone_decides():
    """``@v1`` behaviour: no quality gate, so return on equity is irrelevant."""
    result = evaluate(_evidence(roe_3y=Decimal("-0.50")), PARAMS)
    assert result["status"] == "qualified"
    assert "roe_3y_below_threshold" not in result["reason_codes"]


def test_a_low_return_on_equity_excludes_the_row():
    result = evaluate(_evidence(roe_3y=Decimal("0.02")), QUALITY_PARAMS)
    assert result["status"] == "excluded"
    assert "roe_3y_below_threshold" in result["excluded_reasons"]


def test_a_return_on_equity_exactly_at_the_threshold_passes():
    result = evaluate(_evidence(roe_3y=Decimal("0.08")), QUALITY_PARAMS)
    assert result["status"] == "qualified"
    assert "roe_3y_below_threshold" not in result["reason_codes"]


def test_an_unknown_return_on_equity_blocks_rather_than_excludes():
    result = evaluate(_evidence(roe_3y=None), QUALITY_PARAMS)
    assert result["status"] == "blocked"
    assert "roe_3y_missing" in result["blocking_reasons"]


def test_a_negative_equity_base_is_an_exclusion_not_a_block():
    """A ratio on a negative base is undefined by construction."""
    result = evaluate(
        _evidence(roe_3y=None, equity=Decimal("-1")), QUALITY_PARAMS
    )
    assert result["status"] == "excluded"
    assert "shareholders_equity_not_positive" in result["excluded_reasons"]
    assert "roe_3y_missing" not in result["blocking_reasons"]


def test_the_quality_threshold_must_be_positive():
    with pytest.raises(ValueError):
        ValueInvestingParams(required_margin=Decimal("0.30"), min_roe_3y=Decimal("0"))
    with pytest.raises(ValueError):
        ValueInvestingParams(
            required_margin=Decimal("0.30"), min_roe_3y=Decimal("-0.1")
        )


def test_an_incomplete_three_year_window_blocks_the_row():
    """A partial window would silently turn a three-year test into a two-year one."""
    result = evaluate(_evidence(annual_periods_present=2), QUALITY_PARAMS)
    assert result["status"] == "blocked"
    assert "three_year_window_incomplete" in result["blocking_reasons"]


def test_a_complete_three_year_window_is_not_blocked():
    result = evaluate(_evidence(annual_periods_present=3), QUALITY_PARAMS)
    assert "three_year_window_incomplete" not in result["reason_codes"]


def test_the_report_shows_what_the_quality_gate_removed():
    good = _evidence(symbol="600001.SH", roe_3y=Decimal("0.20"))
    bad = _evidence(symbol="600002.SH", roe_3y=Decimal("0.01"))
    result = screen([good, bad], QUALITY_PARAMS)
    assert result["qualified_symbols"] == ["600001.SH"]
    price_only = result["price_gate_only"]
    assert sorted(price_only["qualified_symbols"]) == ["600001.SH", "600002.SH"]
    assert price_only["removed_by_quality_gate"] == ["600002.SH"]


def test_the_quality_profile_declares_every_dimension():
    profile = quality_profile(QUALITY_PARAMS)
    keys = [dimension["key"] for dimension in profile["dimensions"]]
    assert keys == [
        "how_the_business_earns",
        "product_competitiveness",
        "industry_growth_space",
        "sustained_profitability",
        "value_trap_avoidance",
        "management_integrity",
        "decade_of_advantage",
    ]
    assert profile["gate_enabled"] is True
    covered = {
        dimension["key"]: dimension["status"]
        for dimension in profile["dimensions"]
    }
    # The two dimensions this project cannot answer must say so instead of
    # being quietly presented as covered.
    assert covered["how_the_business_earns"] == "unavailable"
    assert covered["decade_of_advantage"] == "unavailable"
    assert covered["sustained_profitability"] == "covered"


def test_the_quality_profile_reports_a_disabled_gate():
    profile = quality_profile(PARAMS)
    assert profile["gate_enabled"] is False
    assert profile["min_roe_3y"] is None


def test_the_kernel_version_is_reported_and_matches_the_module():
    """The report must carry the kernel version that produced it.

    Two materially different kernels must never share one version string:
    the kernel version is what lets a stored report be traced back to the
    exact rules that produced it.  The definition version (the locked
    parameter set) moves independently -- so the two are allowed to differ,
    but the kernel version must at least be the one the code declares.
    """
    result = screen([_evidence(symbol="600001.SH", roe_3y=Decimal("0.20"))], QUALITY_PARAMS)
    assert result["kernel_version"] == KERNEL_VERSION
    # The kernel version and the strategy definition version are different
    # axes (code behaviour versus locked thresholds) and must not be
    # conflated into one string.
    assert KERNEL_VERSION != VALUE_INVESTING_VERSION


# ------------------------------------------------------------- the catalog


def test_both_versions_stay_registered_and_v1_is_retired():
    db = _session()
    try:
        register_all_strategy_definitions(db)
        db.commit()
        repository = StrategyDefinitionRepository()
        current = repository.get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_VERSION
        )
        superseded = repository.get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_V1_VERSION
        )
        assert current is not None and superseded is not None
        assert current.status == "active" and current.is_active is True
        assert superseded.status == "retired" and superseded.is_active is False
        # The margin of safety never moves between versions.
        assert current.required_margin == Decimal("0.3000000000")
        assert superseded.required_margin == Decimal("0.3000000000")
    finally:
        db.close()


def test_only_the_current_version_carries_the_quality_gate():
    db = _session()
    try:
        register_all_strategy_definitions(db)
        db.commit()
        repository = StrategyDefinitionRepository()
        current = repository.get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_VERSION
        )
        superseded = repository.get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_V1_VERSION
        )
        assert params_from_definition(current).min_roe_3y == Decimal("0.08")
        assert params_from_definition(superseded).min_roe_3y is None
    finally:
        db.close()


def test_the_locked_thresholds_travel_with_the_definition_row():
    db = _session()
    try:
        register_all_strategy_definitions(db)
        db.commit()
        definition = StrategyDefinitionRepository().get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_VERSION
        )
        params = params_from_definition(definition)
        assert params.required_margin == Decimal("0.3000000000")
        assert params.price_factor == Decimal("0.7000000000")
        assert params.max_pe == Decimal("20")
        assert params.max_pb == Decimal("2")
        assert params.min_cash_conversion_3y == Decimal("0.8")
        assert params.top_n == 10
        assert params.max_per_industry == 2
        # The margin is not duplicated inside the JSON blob.
        assert "required_margin" not in (definition.parameters or {})
    finally:
        db.close()


def test_every_superseded_version_stays_registered_but_retired():
    """A past run must still be reproducible from the catalog."""
    db = _session()
    try:
        register_all_strategy_definitions(db)
        db.commit()
        repository = StrategyDefinitionRepository()
        for version in (VALUE_INVESTING_V1_VERSION, VALUE_INVESTING_V2_VERSION):
            definition = repository.get_by_name_version(
                db, name=VALUE_INVESTING_NAME, version=version
            )
            assert definition is not None, version
            assert definition.status == "retired", version
            assert definition.is_active is False, version
            # The margin of safety never moves between versions.
            assert definition.required_margin == Decimal("0.3000000000"), version
    finally:
        db.close()


def test_the_current_version_carries_the_trap_and_growth_thresholds():
    db = _session()
    try:
        register_all_strategy_definitions(db)
        db.commit()
        definition = StrategyDefinitionRepository().get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_VERSION
        )
        params = params_from_definition(definition)
        assert params.min_roe_3y == Decimal("0.08")
        assert params.min_revenue_cagr_3y == Decimal("0.12")
        assert params.min_net_profit_cagr_3y == Decimal("0.15")
        assert params.decline_streak_years == 2
        assert params.max_debt_ratio_rise_3y == Decimal("0.10")
    finally:
        db.close()


def test_the_superseded_quality_version_has_no_trap_or_growth_thresholds():
    db = _session()
    try:
        register_all_strategy_definitions(db)
        db.commit()
        definition = StrategyDefinitionRepository().get_by_name_version(
            db, name=VALUE_INVESTING_NAME, version=VALUE_INVESTING_V2_VERSION
        )
        params = params_from_definition(definition)
        assert params.min_roe_3y == Decimal("0.08")
        assert params.min_revenue_cagr_3y is None
        assert params.min_net_profit_cagr_3y is None
        assert params.decline_streak_years is None
        assert params.max_debt_ratio_rise_3y is None
    finally:
        db.close()
