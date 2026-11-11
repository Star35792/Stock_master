"""Tests for the six-family point-in-time (PIT) audit catalog.

The catalog (``app/factors/pit_audit_catalog.py``) is the place where the
project records *why* a six-family factor may or may not be judged
``point_in_time``.  It is code rather than a manual database edit, so the
invariants below are the ones that keep the pool classifier honest:

* **coverage** -- every factor produced by the six family services must have
  exactly one audit entry.  A missing entry would silently leave the factor
  at the fail-closed default and nobody would notice;
* **justification** -- every entry must carry a non-empty reason.  "We did
  not find a problem" is not a justification for releasing a factor into the
  core pool;
* **hard freeze for consensus estimates** -- analyst consensus / ratings /
  research reports have no first-available timestamp, so any factor built on
  them is ``look_ahead`` and may never enter *any* pool;
* **idempotence and minimal damage** -- annotating twice changes nothing the
  second time, and annotating never touches ``is_active`` / ``status`` /
  ``version``.

The repository helper ``ensure()`` is covered as well: it used to write
``unknown`` back on every registration, which silently erased the audit
conclusions on the next factor run.
"""

from __future__ import annotations

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.factors.pit_audit_catalog import (
    PIT_LOOK_AHEAD,
    PIT_POINT_IN_TIME,
    PIT_UNKNOWN,
    SIX_FAMILY_PIT_AUDIT,
    annotate_six_family_pit_status,
)
from app.models.factor import FactorDefinition
from app.repositories.factor import FactorDefinitionRepository
from app.services.capital_flow_factors import CAPITAL_FLOW_FACTOR_SPECS
from app.services.crowding_factors import FACTOR_NAME as CROWDING_FACTOR_NAME
from app.services.earnings_revision_factors import EARNINGS_REVISION_FACTOR_SPECS
from app.services.macro_factors import MACRO_FACTOR_SPECS
from app.services.policy_event_factors import POLICY_EVENT_FACTOR_SPECS
from app.services.sentiment_factors import SENTIMENT_FACTOR_SPECS

#: Datasets whose content is a *forward-looking analyst estimate*.
CONSENSUS_DATASETS = frozenset({"earnings_revision"})


def _session():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS factor"))
    Base.metadata.create_all(engine, tables=[FactorDefinition.__table__])
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _six_family_factor_names() -> set[str]:
    """Every factor name produced by the six family services."""
    names: set[str] = set()
    for specs in (
        SENTIMENT_FACTOR_SPECS,
        CAPITAL_FLOW_FACTOR_SPECS,
        MACRO_FACTOR_SPECS,
        POLICY_EVENT_FACTOR_SPECS,
        EARNINGS_REVISION_FACTOR_SPECS,
    ):
        names.update(spec.name for spec in specs)
    names.add(CROWDING_FACTOR_NAME)
    return names


def _audit_map() -> dict[str, object]:
    return {entry.factor_name: entry for entry in SIX_FAMILY_PIT_AUDIT}


def _register(db, name: str, *, version: str = "v1") -> FactorDefinition:
    return FactorDefinitionRepository().ensure(
        db,
        name=name,
        display_name=name,
        description="",
        category="sentiment",
        frequency="daily",
        inputs=["market.alternative_data_snapshot"],
        applicable_security_types=["stock"],
        window_days=None,
        version=version,
    )


def test_audit_catalog_covers_every_six_family_factor() -> None:
    """A factor without an audit entry would silently stay fail-closed."""
    audited = set(_audit_map())
    expected = _six_family_factor_names()
    assert audited == expected, {
        "missing": sorted(expected - audited),
        "extra": sorted(audited - expected),
    }


def test_audit_catalog_has_no_duplicate_factor_names() -> None:
    names = [entry.factor_name for entry in SIX_FAMILY_PIT_AUDIT]
    assert len(names) == len(set(names))


def test_every_audit_entry_carries_a_reason_and_datasets() -> None:
    """``reason`` must answer "why"; an empty justification is a hole."""
    for entry in SIX_FAMILY_PIT_AUDIT:
        assert entry.reason.strip(), entry.factor_name
        assert len(entry.reason) >= 20, entry.factor_name
        assert entry.datasets, entry.factor_name


def test_every_audit_entry_uses_a_known_status() -> None:
    allowed = {PIT_POINT_IN_TIME, PIT_LOOK_AHEAD, PIT_UNKNOWN}
    for entry in SIX_FAMILY_PIT_AUDIT:
        assert entry.pit_status in allowed, entry.factor_name


def test_consensus_expectation_factors_are_hard_frozen() -> None:
    """Analyst consensus has no first-available timestamp -> never released."""
    audit = _audit_map()
    for name in ("earnings_revision_1m", "earnings_revision_breadth", "forecast_dispersion"):
        assert audit[name].pit_status == PIT_LOOK_AHEAD, name


def test_no_factor_built_on_consensus_can_be_point_in_time() -> None:
    """The rule is about the *input*, not about the individual factor."""
    for entry in SIX_FAMILY_PIT_AUDIT:
        if CONSENSUS_DATASETS & set(entry.datasets):
            assert entry.pit_status != PIT_POINT_IN_TIME, entry.factor_name


def test_company_announcement_factors_stay_unknown_until_auditable() -> None:
    """Guidance/warnings are PIT-able in principle, but not auditable yet."""
    audit = _audit_map()
    for name in ("guidance_surprise", "profit_warning_risk"):
        assert audit[name].pit_status == PIT_UNKNOWN, name


def test_factors_without_collected_data_stay_unknown() -> None:
    """No data -> no as-of semantics -> fail closed, never assume safety."""
    audit = _audit_map()
    never_collected = (
        "rate_trend_10y",
        "yield_curve_slope",
        "social_financing_impulse",
        "m1_m2_gap",
        "usd_cnh_trend",
        "commodity_inflation_proxy",
        "credit_spread_change",
        "policy_event_score",
        "margin_financing_change_20",
        "etf_flow_industry_20",
        "institutional_crowding_score",
        "northbound_flow_ratio",
    )
    for name in never_collected:
        assert audit[name].pit_status == PIT_UNKNOWN, name


def test_look_ahead_and_unknown_are_not_the_same_verdict() -> None:
    """``look_ahead`` means "proven toxic"; ``unknown`` means "not judged"."""
    audit = _audit_map()
    assert audit["earnings_revision_1m"].pit_status == PIT_LOOK_AHEAD
    assert audit["policy_event_score"].pit_status == PIT_UNKNOWN
    assert audit["earnings_revision_1m"].pit_status != audit["policy_event_score"].pit_status


def test_annotation_writes_the_catalog_verdicts() -> None:
    db = _session()
    audit = _audit_map()
    for name in audit:
        _register(db, name)

    summary = annotate_six_family_pit_status(db)
    db.commit()

    assert summary["missing"] == []
    # 新建定义行默认就是 fail-closed 的 ``unknown``，所以只有「判定不是
    # unknown」的因子才算一次真实变更。
    changed = sum(1 for entry in audit.values() if entry.pit_status != PIT_UNKNOWN)
    assert summary["rows_updated"] == changed
    for row in db.scalars(select(FactorDefinition)).all():
        assert row.pit_status == audit[row.name].pit_status, row.name
    db.close()


def test_annotation_is_idempotent() -> None:
    db = _session()
    for entry in SIX_FAMILY_PIT_AUDIT:
        _register(db, entry.factor_name)

    changed = sum(1 for entry in SIX_FAMILY_PIT_AUDIT if entry.pit_status != PIT_UNKNOWN)
    assert annotate_six_family_pit_status(db)["rows_updated"] == changed
    db.commit()
    assert annotate_six_family_pit_status(db)["rows_updated"] == 0
    db.close()


def test_annotation_only_touches_pit_status() -> None:
    """Activation, lifecycle status and version must survive untouched."""
    db = _session()
    definition = _register(db, "hot_rank_score")
    definition.is_active = False
    definition.status = "retired"
    definition.version = "v1-custom"
    db.commit()

    annotate_six_family_pit_status(db)
    db.commit()

    refreshed = db.scalar(
        select(FactorDefinition).where(FactorDefinition.name == "hot_rank_score")
    )
    assert refreshed is not None
    assert refreshed.pit_status == PIT_POINT_IN_TIME
    assert refreshed.is_active is False
    assert refreshed.status == "retired"
    assert refreshed.version == "v1-custom"
    db.close()


def test_annotation_reports_factors_without_definition_rows() -> None:
    """A missing definition row must be reported, not silently skipped."""
    db = _session()
    _register(db, "hot_rank_score")

    summary = annotate_six_family_pit_status(db)
    db.commit()

    assert summary["missing"]
    assert "hot_rank_score" not in summary["missing"]
    assert summary["rows_updated"] == 1
    db.close()


def test_annotation_updates_every_version_of_a_factor() -> None:
    """A factor may have several versions; all of them carry the verdict."""
    db = _session()
    _register(db, "hot_rank_score", version="v1")
    _register(db, "hot_rank_score", version="v2")

    annotate_six_family_pit_status(db)
    db.commit()

    rows = list(
        db.scalars(select(FactorDefinition).where(FactorDefinition.name == "hot_rank_score"))
    )
    assert len(rows) == 2
    assert {row.pit_status for row in rows} == {PIT_POINT_IN_TIME}
    db.close()


def test_ensure_keeps_an_existing_pit_status() -> None:
    """Re-registering a factor must not erase the audit conclusion."""
    db = _session()
    repository = FactorDefinitionRepository()
    repository.ensure(
        db,
        name="hot_rank_score",
        display_name="Hot rank score",
        description="",
        category="sentiment",
        frequency="daily",
        inputs=["market.alternative_data_snapshot"],
        applicable_security_types=["stock"],
        window_days=None,
        version="v1",
        pit_status=PIT_POINT_IN_TIME,
    )
    db.commit()

    # No ``pit_status`` argument -> keep the audited value.
    repository.ensure(
        db,
        name="hot_rank_score",
        display_name="Hot rank score",
        description="updated description",
        category="sentiment",
        frequency="daily",
        inputs=["market.alternative_data_snapshot"],
        applicable_security_types=["stock"],
        window_days=None,
        version="v1",
    )
    db.commit()

    row = db.scalar(
        select(FactorDefinition).where(FactorDefinition.name == "hot_rank_score")
    )
    assert row is not None
    assert row.pit_status == PIT_POINT_IN_TIME
    assert row.description == "updated description"
    db.close()


def test_ensure_new_definition_falls_back_to_unknown() -> None:
    """A brand new factor has not been audited -> fail closed."""
    db = _session()
    _register(db, "brand_new_factor")
    db.commit()

    row = db.scalar(
        select(FactorDefinition).where(FactorDefinition.name == "brand_new_factor")
    )
    assert row is not None
    assert row.pit_status == PIT_UNKNOWN
    db.close()


def test_ensure_can_still_set_an_explicit_status() -> None:
    """An explicit ``pit_status`` argument still wins."""
    db = _session()
    repository = FactorDefinitionRepository()
    repository.ensure(
        db,
        name="explicit_factor",
        display_name="Explicit",
        description="",
        category="sentiment",
        frequency="daily",
        inputs=[],
        applicable_security_types=["stock"],
        window_days=None,
        version="v1",
        pit_status=PIT_LOOK_AHEAD,
    )
    db.commit()

    row = db.scalar(select(FactorDefinition).where(FactorDefinition.name == "explicit_factor"))
    assert row is not None
    assert row.pit_status == PIT_LOOK_AHEAD
    db.close()
