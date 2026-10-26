"""Tests for the IC-evidence factor catalog.

The evidence catalog registers the factor definitions whose IC evidence is
produced by a dedicated script rather than by the batch profiler:

* the warm-layer price/volume factors at version ``v2-qfq-2y``;
* the value factors derived from ``research.valuation_history``.

Two invariants are asserted here because breaking either one silently
destroys the pool classification:

* the registered ``version`` string must match the version used by the
  persisted evidence **character for character** -- the pool classifier looks
  the definition up by ``(name, version)`` and falls back to ``category="unknown"``
  / ``pit_status="unknown"`` when the lookup misses;
* the definitions must be inactive, because the batch profiler enumerates
  active definitions and would otherwise overwrite the evidence with empty
  cross-sections.
"""

from __future__ import annotations

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.factors.evidence_catalog import (
    PIT_LOOK_AHEAD,
    PIT_POINT_IN_TIME,
    STATUS_RETIRED,
    WARM_PRICE_VOLUME_VERSION,
    all_evidence_factor_specs,
    annotate_price_volume_pit_status,
    register_evidence_factor_definitions,
)
from app.models.factor import FactorDefinition


def _session():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS factor"))
    Base.metadata.create_all(engine, tables=[FactorDefinition.__table__])
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_every_evidence_factor_is_inactive() -> None:
    """Active definitions would let the batch profiler overwrite the evidence."""
    for spec in all_evidence_factor_specs():
        assert spec.is_active is False, spec.name


def test_value_factors_carry_the_audited_pit_status() -> None:
    """``PB_MRQ`` leaks the balance sheet; ``PE_TTM`` does not."""
    status = {
        (spec.name, spec.version): spec.pit_status for spec in all_evidence_factor_specs()
    }
    assert status[("value_earnings_yield", "v2-valhist")] == PIT_POINT_IN_TIME
    assert status[("value_pb_mrq", "v2-valhist")] == PIT_LOOK_AHEAD


def test_retired_versions_are_marked_retired() -> None:
    """The superseded ``v1-valhist`` evidence is kept for audit only."""
    status = {
        (spec.name, spec.version): spec.status for spec in all_evidence_factor_specs()
    }
    assert status[("value_pe_ttm", "v1-valhist")] == STATUS_RETIRED
    assert status[("value_earnings_yield", "v1-valhist")] == STATUS_RETIRED
    assert status[("value_earnings_yield", "v2-valhist")] != STATUS_RETIRED


def test_registration_is_idempotent_and_uses_exact_versions() -> None:
    db = _session()
    register_evidence_factor_definitions(db)
    db.commit()
    register_evidence_factor_definitions(db)
    db.commit()

    rows = list(db.scalars(select(FactorDefinition)).all())
    by_key = {(row.name, row.version): row for row in rows}

    assert ("volume_stability_20", WARM_PRICE_VOLUME_VERSION) in by_key
    assert ("value_earnings_yield", "v2-valhist") in by_key
    assert ("value_pb_mrq", "v2-valhist") in by_key
    assert ("value_pe_ttm", "v1-valhist") in by_key
    # Registering twice must not duplicate the rows.
    assert len(rows) == len(all_evidence_factor_specs())
    db.close()


def test_registration_writes_pit_status_and_status() -> None:
    db = _session()
    register_evidence_factor_definitions(db)
    db.commit()

    earnings = db.scalar(
        select(FactorDefinition).where(
            FactorDefinition.name == "value_earnings_yield",
            FactorDefinition.version == "v2-valhist",
        )
    )
    pb = db.scalar(
        select(FactorDefinition).where(
            FactorDefinition.name == "value_pb_mrq",
            FactorDefinition.version == "v2-valhist",
        )
    )
    assert earnings is not None and pb is not None
    assert earnings.pit_status == PIT_POINT_IN_TIME
    assert pb.pit_status == PIT_LOOK_AHEAD
    assert earnings.is_active is False
    assert earnings.status == "active"
    db.close()


def test_price_volume_definitions_are_annotated_point_in_time() -> None:
    """Price/volume factors use only today's and past prices, so they are safe."""
    db = _session()
    from app.repositories.factor import FactorDefinitionRepository

    repository = FactorDefinitionRepository()
    repository.ensure(
        db,
        name="volume_stability_20",
        display_name="Volume stability",
        description="",
        category="price_volume_risk",
        frequency="daily",
        inputs=["market.daily_bar.volume"],
        applicable_security_types=["stock"],
        window_days=20,
        version="v1-qfq",
    )
    db.commit()
    assert db.scalar(
        select(FactorDefinition).where(FactorDefinition.name == "volume_stability_20")
    ).pit_status == "unknown"

    assert annotate_price_volume_pit_status(db) == 1
    db.commit()
    assert db.scalar(
        select(FactorDefinition).where(FactorDefinition.name == "volume_stability_20")
    ).pit_status == PIT_POINT_IN_TIME
    # The annotation is idempotent.
    assert annotate_price_volume_pit_status(db) == 0
    db.close()
