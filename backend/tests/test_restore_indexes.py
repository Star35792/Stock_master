"""Regression tests for the index-restore coverage.

The bug these tests pin down
----------------------------
``scripts.maintenance.restore_indexes`` read its declarations from
``Base.metadata.tables[...].indexes``.  SQLAlchemy keeps a
``UniqueConstraint`` in ``table.constraints`` instead, so the 22 unique
constraints the ORM declares were invisible to the script.  DuckDB only
materialises a ``UNIQUE`` constraint as an index when the table is created
with ``CREATE TABLE ... UNIQUE``, so every CTAS-based rebuild dropped them
and ``ON CONFLICT`` lost its deduplication guarantee -- silently, because
the script reported "nothing missing".

``lake.release`` is the concrete casualty: it carried four duplicated
``release_id`` values that no constraint was there to reject.
"""

from __future__ import annotations

import duckdb

from scripts.maintenance.restore_indexes import (
    actual_indexes,
    declared_indexes,
    plan_indexes,
)


def test_unique_constraints_are_declared():
    """Every ORM ``UniqueConstraint`` must appear in the declaration list."""
    specs = declared_indexes()
    unique = [spec for spec in specs if spec.unique]
    assert unique, "no unique declaration was found at all"

    names = {(spec.table, spec.name) for spec in unique}
    assert ("lake.release", "uq_release_release_id") in names

    # The set must be larger than the plain ``table.indexes`` collection,
    # otherwise the constraints are still being missed.
    import app.models  # noqa: F401
    from app.database.base import Base

    plain_unique = [
        index
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.unique
    ]
    assert len(unique) > len(plain_unique)


def test_constraint_columns_are_resolved():
    """A constraint's columns must be resolvable, not empty.

    An empty column list would make the equivalence check compare against
    nothing and wrongly report the index as covered.
    """
    for spec in declared_indexes():
        if spec.unique:
            assert spec.columns, f"{spec.table}.{spec.name} has no columns"


def test_lake_release_unique_index_is_detected_as_covered(tmp_path):
    """With the unique index present, nothing may be reported missing."""
    from sqlalchemy import create_engine

    path = tmp_path / "rel.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA lake")
    con.execute("CREATE TABLE lake.release (release_id VARCHAR)")
    con.execute("CREATE UNIQUE INDEX uq_release_release_id ON lake.release (release_id)")
    con.close()

    engine = create_engine(f"duckdb:///{path.as_posix()}")
    try:
        missing, covered = plan_indexes(declared_indexes(), actual_indexes(engine))
    finally:
        engine.dispose()

    release_specs = [
        spec for spec in missing + covered if spec.table == "lake.release"
    ]
    assert release_specs, "the lake.release declaration was not considered"
    assert not [
        spec for spec in missing if spec.table == "lake.release"
    ], "the existing unique index was not recognised as equivalent"


def test_missing_unique_index_is_reported(tmp_path):
    """Without the index, the declaration must be reported as missing."""
    from sqlalchemy import create_engine

    path = tmp_path / "rel2.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA lake")
    con.execute("CREATE TABLE lake.release (release_id VARCHAR)")
    con.close()

    engine = create_engine(f"duckdb:///{path.as_posix()}")
    try:
        missing, _covered = plan_indexes(declared_indexes(), actual_indexes(engine))
    finally:
        engine.dispose()

    assert [
        spec for spec in missing if spec.table == "lake.release"
    ], "the absent unique index was not reported as missing"
