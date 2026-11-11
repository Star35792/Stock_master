"""Compact the control-plane DuckDB by reclaiming unreclaimed free space.

Why this script exists
----------------------
DuckDB never shrinks a database file.  When rows are deleted (or a slimming
run replaces a table with a smaller hot window), the freed blocks stay in the
file as unreclaimed space.  Writing new data reuses that space, so the file
stops growing -- but it never shrinks either.

In this repository the control plane reached 2.0 GiB while its live data was
only about 310 MiB.  Roughly 90% of the file was free space left behind by
earlier slimming runs and by the valuation-history sweep that stored whole
supplier response bodies.

``slim_metadata_db.py`` cannot fix this: it rebuilds the file but keeps only a
hot window of the big tables, so it both moves data and compacts.  This script
does the opposite -- it keeps **every row exactly as it is** and only
reclaims the dead space.

Safety design
--------------
This operation is a rebuild, so the following failure modes are addressed
explicitly:

* ``CREATE TABLE AS SELECT`` drops primary-key sequences, column defaults and
  unique indexes.  All three are copied back, reusing the same helpers that
  ``slim_metadata_db.py`` uses (which were hardened for the WAL-replay
  incident of 2026-11-06).
* DuckDB cannot replay a WAL entry that carries a default expression
  (duckdb/duckdb#18259).  The rebuild therefore checkpoints before the
  original file is replaced, so a crash can never strand the database.
* The original file is kept as a timestamped backup until the operator
  deletes it; the swap is atomic (rename), never an in-place overwrite.
* A row-count fingerprint of every table is compared before and after.  The
  new file is only published when the fingerprints match exactly.

Usage
-----
    python -m scripts.maintenance.compact_control_plane --dry-run
    python -m scripts.maintenance.compact_control_plane
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "stock_master.duckdb"

#: Tables that must never be rebuilt.  They are excluded because they are
#: either tiny control tables (the rebuild gives no benefit) or because a
#: rebuild would drop semantics that the helpers below cannot restore.
_SKIP_TABLES: frozenset[tuple[str, str]] = frozenset()


def _table_list(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """Return every base table in the database, ordered deterministically."""
    rows = con.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_catalog = 'source'
          AND table_type = 'BASE TABLE'
          AND table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
        """
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _fingerprint(
    con: duckdb.DuckDBPyConnection, tables: list[tuple[str, str]], catalog: str = "source"
) -> dict[str, int]:
    """Return the row count of every table, for before/after comparison.

    ``catalog`` names the attached database the tables live in.  The default
    matches the rebuild coordinator, where the original data is attached as
    ``source``; a standalone connection on the published file passes
    ``catalog=None`` to read from its own default catalog.
    """
    counts: dict[str, int] = {}
    for schema, table in tables:
        qualified = (
            f'"{catalog}"."{schema}"."{table}"' if catalog else f'"{schema}"."{table}"'
        )
        row = con.execute(f"SELECT count(*) FROM {qualified}").fetchone()
        counts[f"{schema}.{table}"] = int(row[0])
    return counts


def _fingerprint_catalog(
    con: duckdb.DuckDBPyConnection, catalog: str
) -> dict[str, int]:
    """Fingerprint every table of an attached catalog."""
    rows = con.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_catalog = ?
          AND table_type = 'BASE TABLE'
          AND table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
        """,
        [catalog],
    ).fetchall()
    return _fingerprint(con, [(r[0], r[1]) for r in rows], catalog=catalog)


def _total_rows(counts: dict[str, int]) -> int:
    return sum(counts.values())


def _live_bytes(con: duckdb.DuckDBPyConnection, tables: list[tuple[str, str]]) -> int:
    """Estimate the live bytes of the database from its block usage."""
    total_blocks = 0
    for schema, table in tables:
        try:
            row = con.execute(
                "SELECT count(DISTINCT block_id) "
                f"FROM pragma_storage_info('source.{schema}.{table}')"
            ).fetchone()
            total_blocks += int(row[0])
        except duckdb.Error:
            continue
    return total_blocks * 256 * 1024


def _copy_sequences(con: duckdb.DuckDBPyConnection) -> int:
    """Copy sequence DDL from the source catalog into the target catalog.

    The stored DDL has no catalog qualifier, so it is rewritten to point at
    the target catalog explicitly.  Without the rewrite DuckDB resolves the
    sequence in the *source* catalog, and the target table's default would
    then reference a sequence that does not exist in its own database.
    """
    existing = {
        r[0]
        for r in con.execute(
            "SELECT sequence_name FROM duckdb_sequences() WHERE database_name = 'target'"
        ).fetchall()
    }
    created = 0
    rows = con.execute(
        "SELECT sequence_name, sql FROM duckdb_sequences() WHERE database_name = 'source'"
    ).fetchall()
    for name, sql in rows:
        if name in existing:
            continue
        qualified = _qualify_sequence_ddl(sql, name)
        try:
            con.execute(qualified)
            created += 1
        except duckdb.Error as exc:
            logger.warning("Failed to copy sequence %s: %s", name, exc)
    return created


def _qualify_sequence_ddl(sql: str, name: str) -> str:
    """Rewrite ``CREATE SEQUENCE x`` into ``CREATE SEQUENCE target.x``.

    DuckDB accepts a catalog-qualified sequence name in ``CREATE SEQUENCE``,
    which is the only way to place the sequence in the target catalog while
    the source catalog is attached.
    """
    pattern = re.compile(
        r"(CREATE\s+(?:OR\s+REPLACE\s+)?SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?)"
        + re.escape(name),
        re.IGNORECASE,
    )
    return pattern.sub(r"\1target." + name, sql, count=1)


def _copy_defaults(
    con: duckdb.DuckDBPyConnection, schema: str, table: str
) -> None:
    """Recreate column defaults on the target table.

    ``CREATE TABLE AS SELECT`` drops every default.  Without this the ORM
    cannot insert rows that omit a defaulted column, and -- worse -- the
    missing default is not recorded in the WAL, so a crash right after the
    rebuild leaves a table whose defaults silently differ from the original.
    """
    full_name = f'target."{schema}"."{table}"'
    rows = con.execute(
        """
        SELECT column_name, column_default
        FROM information_schema.columns
        WHERE table_catalog = 'source'
          AND table_schema = ?
          AND table_name = ?
          AND column_default IS NOT NULL
        """,
        [schema, table],
    ).fetchall()
    for column, default_expr in rows:
        try:
            con.execute(
                f'ALTER TABLE {full_name} ALTER COLUMN "{column}" SET DEFAULT {default_expr}'
            )
        except duckdb.Error as exc:
            logger.warning("Failed to set default on %s.%s: %s", schema, table, exc)


def _copy_unique_indexes(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> None:
    """Recreate unique indexes on the target table.

    DuckDB keeps ``PRIMARY KEY`` in ``duckdb_constraints()`` and standalone
    unique indexes in ``duckdb_indexes()``.  Both are copied, because the
    repository layer relies on ``ON CONFLICT`` targets that reference them.
    """
    existing = {
        r[0]
        for r in con.execute(
            """
            SELECT index_name
            FROM duckdb_indexes()
            WHERE database_name = 'target'
              AND schema_name = ?
              AND table_name = ?
            """,
            [schema, table],
        ).fetchall()
    }

    index_rows = con.execute(
        """
        SELECT index_name, sql, is_primary
        FROM duckdb_indexes()
        WHERE database_name = 'source'
          AND schema_name = ?
          AND table_name = ?
          AND is_unique = true
        """,
        [schema, table],
    ).fetchall()
    for idx_name, sql, is_primary in index_rows:
        if is_primary or idx_name in existing:
            continue
        try:
            con.execute(_qualify_index_ddl(sql, schema, table))
            logger.info("Copied unique index %s on %s.%s", idx_name, schema, table)
        except duckdb.Error as exc:
            logger.warning(
                "Failed to copy unique index %s on %s.%s: %s", idx_name, schema, table, exc
            )

    constraint_rows = con.execute(
        """
        SELECT constraint_column_names
        FROM duckdb_constraints()
        WHERE database_name = 'source'
          AND schema_name = ?
          AND table_name = ?
          AND constraint_type = 'UNIQUE'
        """,
        [schema, table],
    ).fetchall()
    for (cols,) in constraint_rows:
        if not cols:
            continue
        idx_name = f"ix_compact_{schema}_{table}_{'_'.join(cols)}"
        if idx_name in existing:
            continue
        col_sql = ", ".join(f'"{c}"' for c in cols)
        try:
            con.execute(
                f'CREATE UNIQUE INDEX "{idx_name}" ON target."{schema}"."{table}" ({col_sql})'
            )
            existing.add(idx_name)
            logger.info("Created unique index %s on %s.%s", idx_name, schema, table)
        except duckdb.Error as exc:
            logger.warning(
                "Failed to create unique index on %s.%s (%s): %s", schema, table, col_sql, exc
            )


def _qualify_index_ddl(sql: str, schema: str, table: str) -> str:
    """Rewrite an index DDL so it targets the target catalog's table."""
    qualified_table = f'target."{schema}"."{table}"'
    pattern = re.compile(
        r"(CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\S+\s+ON\s+)"
        + re.escape(schema)
        + r"\."
        + re.escape(table),
        re.IGNORECASE,
    )
    return pattern.sub(r"\1" + qualified_table, sql, count=1)


def _restore_all_defaults(con: duckdb.DuckDBPyConnection) -> None:
    """Retry every default after the target sequences exist.

    A default expression may reference a sequence, so the sequence has to be
    created before the default can bind to it.  This pass runs after the
    sequence copy and re-applies anything that failed the first time.
    """
    rows = con.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_catalog = 'source'
          AND table_type = 'BASE TABLE'
          AND table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
        """
    ).fetchall()
    for schema, table in rows:
        _copy_defaults(con, schema, table)


def _rebuild(con: duckdb.DuckDBPyConnection, tables: list[tuple[str, str]]) -> None:
    """Copy every table into the target, preserving defaults and unique indexes.

    The order matters: the sequences must exist in the target catalog before
    the column defaults are applied, because a default such as
    ``nextval('x_id_seq')`` can only bind to a sequence of its own database.
    The schemas are all created first as well, so that an index on a table of
    a not-yet-used schema does not hit a stale catalog cache.
    """
    for schema, _table in tables:
        con.execute(f'CREATE SCHEMA IF NOT EXISTS target."{schema}"')

    for schema, table in tables:
        con.execute(
            f'CREATE TABLE target."{schema}"."{table}" '
            f'AS SELECT * FROM source."{schema}"."{table}"'
        )
        logger.info("Copied %s.%s", schema, table)

    for schema, table in tables:
        _copy_defaults(con, schema, table)
        _copy_unique_indexes(con, schema, table)


def _restore_unique_indexes_from_orm(db_path: Path) -> int:
    """Recreate the ORM's unique constraints as unique indexes.

    A control plane that was rebuilt by an earlier CTAS-based slim lost its
    unique indexes: DuckDB stores a ``UNIQUE`` constraint in
    ``duckdb_constraints()`` and only materialises it as an index when the
    table is created with ``CREATE TABLE ... UNIQUE``.  Every rebuild path
    therefore has to restore them explicitly, otherwise the repository
    layer's ``ON CONFLICT`` upserts stop deduplicating silently.
    """
    import app.models  # noqa: F401
    from app.database.base import Base

    engine = create_engine(f"duckdb:///{db_path.as_posix()}")
    created = 0
    try:
        for table in Base.metadata.sorted_tables:
            for constraint in table.constraints:
                if type(constraint).__name__ != "UniqueConstraint":
                    continue
                columns = list(constraint.columns.keys())
                if not columns:
                    continue
                schema = table.schema or "main"
                with engine.begin() as conn:
                    exists = conn.execute(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_catalog = current_database() "
                            "AND table_schema=:schema AND table_name=:table"
                        ),
                        {"schema": schema, "table": table.name},
                    ).scalar_one()
                if not exists:
                    continue
                idx_name = f"ix_compact_{schema}_{table.name}_{'_'.join(columns)}"
                with engine.begin() as conn:
                    already = conn.execute(
                        text(
                            "SELECT count(*) FROM duckdb_indexes() "
                            "WHERE database_name = current_database() AND index_name = :name"
                        ),
                        {"name": idx_name},
                    ).scalar_one()
                if already:
                    continue
                col_sql = ", ".join(f'"{c}"' for c in columns)
                # An equivalent unique index may already exist under a
                # different name (the earlier slimming runs created
                # ``ix_slim_*`` indexes).  Creating a second copy would
                # double the space every unique constraint occupies without
                # adding any guarantee, so the existing one is reused.
                with engine.begin() as conn:
                    equivalent = conn.execute(
                        text(
                            "SELECT count(*) FROM duckdb_indexes() "
                            "WHERE database_name = current_database() "
                            "AND schema_name = :schema AND table_name = :table "
                            "AND is_unique = true"
                        ),
                        {"schema": schema, "table": table.name},
                    ).scalar_one()
                if equivalent:
                    logger.info(
                        "Reusing existing unique index on %s.%s (%d found)",
                        schema,
                        table.name,
                        equivalent,
                    )
                    continue
                col_sql = ", ".join(f'"{c}"' for c in columns)
                # Each index gets its own transaction: a failing ``CREATE
                # UNIQUE INDEX`` aborts the transaction it runs in, and a
                # shared transaction would take every later index down with
                # it.
                try:
                    with engine.begin() as conn:
                        conn.execute(
                            text(
                                f'CREATE UNIQUE INDEX "{idx_name}" '
                                f'ON "{schema}"."{table.name}" ({col_sql})'
                            )
                        )
                    created += 1
                    logger.info("Created unique index %s", idx_name)
                except (duckdb.Error, IntegrityError) as exc:
                    # The index cannot be created when the existing rows
                    # already violate the constraint.  That is a data
                    # problem, not a rebuild problem, so it is reported
                    # but never blocks the compaction: the operator can
                    # decide separately whether to deduplicate the rows
                    # or to relax the constraint.
                    logger.warning(
                        "Skipped unique index %s on %s.%s: %s",
                        idx_name,
                        schema,
                        table.name,
                        exc,
                    )
    finally:
        engine.dispose(close=True)
    return created


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to the control-plane DuckDB file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the reclaimable space without rebuilding anything",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip creating a timestamped backup (not recommended)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        return 1

    for stale in (db_path.with_suffix(".duckdb.wal"), db_path.with_suffix(".duckdb.tmp")):
        if stale.exists():
            logger.warning("Removing stale sidecar file: %s", stale)
            stale.unlink()

    # One coordinator connection owns both catalogs for the whole run.  The
    # source is ATTACHed READ_ONLY, so the rebuild cannot write through it.
    #
    # Note: this deliberately avoids ``app.database.session``, whose module
    # level engine opens the control plane read-write on import.  DuckDB
    # refuses a second handle on the same file with a different configuration,
    # so importing it here would make every later read-only open fail.
    con = duckdb.connect(":memory:")
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"ATTACH '{db_path.as_posix()}' AS source (READ_ONLY)")

    try:
        tables = _table_list(con)
        before_counts = _fingerprint(con, tables)
        live_bytes = _live_bytes(con, tables)
        original_size = db_path.stat().st_size

        logger.info("Tables: %d", len(tables))
        logger.info("Rows:   %d", _total_rows(before_counts))
        logger.info("File:   %.2f MiB", original_size / 1024 / 1024)
        logger.info("Live:   %.2f MiB (estimated from block usage)", live_bytes / 1024 / 1024)
        logger.info(
            "Reclaimable: %.2f MiB (%.1f%%)",
            max(0.0, (original_size - live_bytes) / 1024 / 1024),
            max(0.0, (original_size - live_bytes) / original_size * 100),
        )

        if args.dry_run:
            logger.info("Dry run: nothing written")
            return 0

        backup_path: Path | None = None
        if not args.skip_backup:
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"{db_path.stem}-pre-compact-{stamp}{db_path.suffix}"
            shutil.copy2(db_path, backup_path)
            logger.info("Backup created: %s", backup_path)

        tmp_path = db_path.with_suffix(".duckdb.compacting")
        for stale in (tmp_path, tmp_path.with_suffix(".duckdb.wal")):
            if stale.exists():
                stale.unlink()

        started = time.perf_counter()
        con.execute(f"ATTACH '{tmp_path.as_posix()}' AS target")
        # Sequences first: the column defaults applied during the rebuild
        # reference them and can only bind to a sequence of their own catalog.
        sequences = _copy_sequences(con)
        if sequences:
            logger.info("Copied %d sequence(s)", sequences)
        _rebuild(con, tables)
        _restore_all_defaults(con)
        con.execute("CHECKPOINT target")
        logger.info("Rebuild finished in %.1fs", time.perf_counter() - started)

        # Verify the rebuilt file before it replaces the original.
        after_counts = _fingerprint_catalog(con, "target")
        mismatches = {
            name: (before_counts[name], after_counts.get(name))
            for name in before_counts
            if before_counts[name] != after_counts.get(name)
        }
        if mismatches:
            logger.error("Row counts changed, refusing to publish: %s", mismatches)
            con.execute("DETACH target")
            tmp_path.unlink(missing_ok=True)
            return 2
        logger.info("Row counts match for all %d tables", len(tables))

        con.execute("DETACH source")
        con.execute("DETACH target")
        con.close()

        compact_size = tmp_path.stat().st_size
        swapped = db_path.with_suffix(".duckdb.prev")
        if swapped.exists():
            swapped.unlink()
        db_path.rename(swapped)
        tmp_path.rename(db_path)

        # The published file is verified *before* the ORM engine is involved.
        # Importing ``app.database.session`` creates a module level engine that
        # opens the control plane read-write, and DuckDB then refuses any
        # other handle on the same file.  The verification therefore has to
        # happen first, while only DuckDB owns the file.
        verify_con = duckdb.connect(str(db_path), read_only=True)
        try:
            final_counts = _fingerprint(verify_con, tables, catalog=None)
        finally:
            verify_con.close()
        if final_counts != before_counts:
            logger.error("Published file does not match the original row counts")
            swapped.rename(db_path)
            return 3

        # CTAS drops id sequences and column defaults.  They are restored
        # above the retained maxima by the shared helper, and the WAL is
        # folded into the file right after so the database stays replayable
        # even if the process is killed immediately afterwards
        # (duckdb/duckdb#18259).
        # ``ensure_duckdb_sequences`` reads the ORM metadata to learn which
        # columns are backed by a sequence.  Importing the model package
        # guarantees the metadata is complete regardless of import order.
        import app.models  # noqa: F401
        from app.database.session import checkpoint_duckdb, ensure_duckdb_sequences

        repaired_engine = create_engine(f"duckdb:///{db_path.as_posix()}")
        try:
            ensure_duckdb_sequences(repaired_engine)
            checkpoint_duckdb(repaired_engine)
        finally:
            repaired_engine.dispose(close=True)

        # The unique constraints of the ORM are restored as unique indexes.
        # They are missing from any control plane that was rebuilt through
        # CTAS, and their absence silently disables the repositories'
        # ``ON CONFLICT`` upserts.
        unique_indexes = _restore_unique_indexes_from_orm(db_path)
        if unique_indexes:
            logger.info("Restored %d unique index(es)", unique_indexes)

        swapped.unlink()

        summary = {
            "database": str(db_path),
            "backup": str(backup_path) if backup_path else None,
            "original_size_bytes": original_size,
            "compact_size_bytes": compact_size,
            "reclaimed_bytes": original_size - compact_size,
            "reclaimed_pct": (original_size - compact_size) / original_size * 100
            if original_size
            else 0.0,
            "live_bytes_estimate": live_bytes,
            "tables": len(tables),
            "rows": _total_rows(before_counts),
            "per_table_rows": before_counts,
        }
        summary_path = (
            db_path.parent / f"compact_summary-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
        )
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info("Compacted size: %.2f MiB", compact_size / 1024 / 1024)
        logger.info(
            "Reclaimed: %.2f MiB (%.1f%%)",
            (original_size - compact_size) / 1024 / 1024,
            summary["reclaimed_pct"],
        )
        logger.info("Summary written to %s", summary_path)
        return 0
    finally:
        try:
            con.close()
        except duckdb.Error:
            pass


if __name__ == "__main__":
    sys.exit(main())
