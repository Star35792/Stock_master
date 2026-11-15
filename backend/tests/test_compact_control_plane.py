"""Regression tests for the control-plane compaction accounting.

The bug these tests pin down
----------------------------
``compact_control_plane`` reported its reclaimable space from
``_live_bytes``, which counted only the ``block_id`` column of
``pragma_storage_info``.  A compressed segment spills into further blocks
that DuckDB lists in ``additional_block_ids``, and those were ignored, so
the estimate understated the live data by a factor of 7.3 on the production
control plane.  The script then advertised 86% of a 2.5 GiB database as
reclaimable when the file held no dead block at all.

``PRAGMA database_size`` is the authoritative source: ``free_blocks`` counts
the blocks a delete released but never returned to the filesystem.  The
reporting path now uses it.
"""

from __future__ import annotations

import duckdb
import pytest

from scripts.maintenance.compact_control_plane import (
    _copy_indexes,
    _free_bytes,
    _live_bytes,
    _qualify_index_ddl,
    _restore_unique_indexes_from_orm,
    _table_list,
    main,
)


def _write_db(path, delete_ratio: int = 0):
    """Write a table, optionally delete a fraction of its rows."""
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE t AS SELECT range AS id, 'payload_' || range AS payload "
        "FROM range(400000)"
    )
    if delete_ratio:
        con.execute(f"DELETE FROM t WHERE id < {400000 * delete_ratio}")
    con.execute("CHECKPOINT")
    con.close()
    return path


@pytest.fixture()
def db_with_dead_blocks(tmp_path):
    """A database whose rows were deleted, leaving unreclaimed free blocks."""
    return _write_db(tmp_path / "dead.duckdb", delete_ratio=0.75)


@pytest.fixture()
def db_without_dead_blocks(tmp_path):
    """A freshly written database: every block is live."""
    return _write_db(tmp_path / "packed.duckdb")


def _attach(path):
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{path.as_posix()}' AS source (READ_ONLY)")
    return con


def test_free_bytes_detects_unreclaimed_space(db_with_dead_blocks):
    """A delete that released blocks must show up as free space."""
    con = _attach(db_with_dead_blocks)
    try:
        free = _free_bytes(con, "source")
    finally:
        con.close()
    assert free > 0, "deleted rows must leave unreclaimed blocks behind"


def test_free_bytes_is_zero_for_packed_database(db_without_dead_blocks):
    """A database without deletes has nothing to reclaim."""
    con = _attach(db_without_dead_blocks)
    try:
        free = _free_bytes(con, "source")
    finally:
        con.close()
    assert free == 0


def test_free_bytes_selects_the_named_catalog(db_without_dead_blocks):
    """The pragma returns one row per catalog; the wrong row must be ignored.

    The coordinator connection always has an in-memory catalog alongside the
    attached one, and its row comes first and reports zero bytes.  Reading
    the first row instead of the row named ``source`` would silently report
    "nothing to reclaim" for a database that does have dead blocks.
    """
    con = _attach(db_without_dead_blocks)
    try:
        rows = con.execute("PRAGMA database_size").fetchall()
        assert len(rows) > 1, "the pragma must report more than one catalog"
        assert rows[0][0] != "source", "the memory catalog is expected first"
        assert _free_bytes(con, "source") == 0
        assert _free_bytes(con, "memory") == 0
    finally:
        con.close()


def test_free_bytes_finds_dead_blocks_even_with_a_memory_catalog_first(
    db_with_dead_blocks,
):
    """The named-catalog lookup must find the dead blocks of the attached file.

    This is the exact failure mode of reading the pragma by position.
    """
    con = _attach(db_with_dead_blocks)
    try:
        assert _free_bytes(con, "source") > 0
    finally:
        con.close()


def test_live_bytes_accounts_for_compressed_spill_blocks(db_without_dead_blocks):
    """``_live_bytes`` must not undercount the compressed spill blocks.

    The old implementation counted only ``block_id`` and therefore reported
    a fraction of the real live data.
    """
    con = _attach(db_without_dead_blocks)
    try:
        tables = _table_list(con)
        live = _live_bytes(con, tables)
        spill = con.execute(
            "SELECT count(*) FROM pragma_storage_info('source.main.t') "
            "WHERE additional_block_ids IS NOT NULL "
            "AND additional_block_ids NOT IN ('[]', '')"
        ).fetchone()[0]
        distinct_only = con.execute(
            "SELECT count(DISTINCT block_id) FROM pragma_storage_info('source.main.t')"
        ).fetchone()[0]
    finally:
        con.close()
    assert live > 0
    if spill:
        # A compressed column spilled into extra blocks: the estimate has to
        # include them, otherwise it is systematically too small.
        assert live >= distinct_only * 256 * 1024


def test_live_bytes_never_exceeds_the_file_size(db_without_dead_blocks):
    """The live estimate must stay within the bounds of the actual file."""
    size = db_without_dead_blocks.stat().st_size
    con = _attach(db_without_dead_blocks)
    try:
        live = _live_bytes(con, _table_list(con))
    finally:
        con.close()
    assert 0 < live <= size


def test_compaction_skips_database_without_dead_blocks(db_without_dead_blocks):
    """A full database must not be rebuilt just to reshuffle live blocks."""
    path = db_without_dead_blocks
    original_size = path.stat().st_size

    assert main(["--db-path", str(path)]) == 0

    assert path.stat().st_size == original_size
    assert not path.with_suffix(".duckdb.compacting").exists()
    assert not path.with_suffix(".duckdb.prev").exists()


def test_compaction_reports_prior_artifact_without_deleting_it(db_without_dead_blocks):
    """An earlier interrupted swap needs inspection even if no space is free."""
    path = db_without_dead_blocks
    previous = path.with_suffix(".duckdb.prev")
    previous.write_bytes(b"prior state")

    assert main(["--db-path", str(path)]) == 1
    assert previous.read_bytes() == b"prior state"


def test_compaction_refuses_another_writer(db_without_dead_blocks):
    """The file swap must not run while another process owns the writer lock."""
    from app.utils.duckdb_write_lock import duckdb_write_lock

    path = db_without_dead_blocks
    lock_path = path.parent / "duckdb_write.lock"
    with duckdb_write_lock(lock_path, timeout_seconds=0):
        assert main(["--db-path", str(path)]) == 1
    assert not path.with_suffix(".duckdb.compacting").exists()


def test_compaction_preserves_regular_indexes(db_with_dead_blocks):
    """A published rebuild must keep source indexes used by queries."""
    path = db_with_dead_blocks
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE INDEX ix_t_payload ON t (payload)")
        con.execute("CHECKPOINT")

    assert main(["--db-path", str(path)]) == 0

    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 100000
        indexes = con.execute(
            "SELECT index_name, is_unique FROM duckdb_indexes() "
            "WHERE table_name = 't'"
        ).fetchall()
    assert ("ix_t_payload", False) in indexes
    assert not path.with_suffix(".duckdb.prev").exists()


def test_index_ddl_rewrite_handles_quoted_table_names():
    """DuckDB quotes reserved table names in stored index DDL."""
    sql = 'CREATE UNIQUE INDEX uq_release_release_id ON lake."release"(release_id);'
    assert _qualify_index_ddl(sql, "lake", "release") == (
        'CREATE UNIQUE INDEX uq_release_release_id '
        'ON target."lake"."release"(release_id);'
    )
    with pytest.raises(ValueError, match="cannot qualify index DDL"):
        _qualify_index_ddl(sql, "market", "daily_bar")


def test_copy_index_on_quoted_release_table(tmp_path):
    """The index must be created in the target catalog, not on the source."""
    source = tmp_path / "source.duckdb"
    target = tmp_path / "target.duckdb"
    with duckdb.connect(str(source)) as con:
        con.execute("CREATE SCHEMA lake")
        con.execute('CREATE TABLE lake."release" (release_id VARCHAR)')
        con.execute(
            'CREATE UNIQUE INDEX uq_release_release_id '
            'ON lake."release" (release_id)'
        )
    with duckdb.connect(":memory:") as con:
        con.execute(f"ATTACH '{source.as_posix()}' AS source (READ_ONLY)")
        con.execute(f"ATTACH '{target.as_posix()}' AS target")
        con.execute('CREATE SCHEMA target."lake"')
        con.execute(
            'CREATE TABLE target."lake"."release" '
            'AS SELECT * FROM source."lake"."release"'
        )
        _copy_indexes(con, "lake", "release")
        indexes = con.execute(
            "SELECT database_name,index_name FROM duckdb_indexes() "
            "WHERE index_name = 'uq_release_release_id' ORDER BY database_name"
        ).fetchall()
    assert indexes == [
        ("source", "uq_release_release_id"),
        ("target", "uq_release_release_id"),
    ]


def test_failed_index_copy_keeps_source_and_reclaims_temp(
    db_without_dead_blocks, monkeypatch,
):
    """An index copy error must leave the source intact and no large temp file."""
    path = db_without_dead_blocks
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE INDEX ix_t_payload ON t (payload)")
        con.execute("CHECKPOINT")
    original_size = path.stat().st_size
    monkeypatch.setattr(
        "scripts.maintenance.compact_control_plane._free_bytes",
        lambda _con: 256 * 1024,
    )
    monkeypatch.setattr(
        "scripts.maintenance.compact_control_plane._qualify_index_ddl",
        lambda _sql, _schema, _table: "INVALID INDEX DDL",
    )

    with pytest.raises(duckdb.Error):
        main(["--db-path", str(path)])

    assert path.stat().st_size == original_size
    assert not path.with_suffix(".duckdb.compacting").exists()
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 400000
        assert con.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE index_name = 'ix_t_payload'"
        ).fetchone()[0] == 1


def test_checkpoint_failure_preserves_original_for_recovery(
    db_with_dead_blocks, monkeypatch,
):
    """The verified swap must retain its original if WAL folding fails."""
    path = db_with_dead_blocks
    def fail_checkpoint(_engine):
        raise RuntimeError("checkpoint failed")

    monkeypatch.setattr(
        "scripts.maintenance.compact_control_plane._checkpoint_required",
        fail_checkpoint,
    )

    with pytest.raises(RuntimeError, match="checkpoint failed"):
        main(["--db-path", str(path)])

    previous = path.with_suffix(".duckdb.prev")
    assert previous.is_file()
    assert path.is_file()
    with duckdb.connect(str(previous), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 100000


def test_unique_restore_matches_columns_not_just_table(tmp_path):
    """An unrelated unique index cannot satisfy ``lake.release.release_id``."""
    path = tmp_path / "release.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA lake")
        con.execute("CREATE TABLE lake.release (dataset VARCHAR, release_id VARCHAR)")
        con.execute("INSERT INTO lake.release VALUES ('a', 'r1'), ('b', 'r2')")
        con.execute("CREATE UNIQUE INDEX uq_dataset ON lake.release (dataset)")

    assert _restore_unique_indexes_from_orm(path) == 1

    with duckdb.connect(str(path), read_only=True) as con:
        rows = con.execute(
            "SELECT sql FROM duckdb_indexes() WHERE table_name = 'release' "
            "AND is_unique = true"
        ).fetchall()
    assert any("release_id" in row[0] for row in rows)


def test_unique_restore_rejects_conflicting_rows(tmp_path):
    """A duplicate key must stop publication rather than lose deduplication."""
    path = tmp_path / "conflict.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA lake")
        con.execute("CREATE TABLE lake.release (release_id VARCHAR)")
        con.execute("INSERT INTO lake.release VALUES ('same'), ('same')")

    with pytest.raises(RuntimeError, match="declared unique indexes not restored"):
        _restore_unique_indexes_from_orm(path)
