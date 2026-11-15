"""Shrink the control-plane DuckDB by off-loading historical rows to Parquet.

This is Phase 2B of the storage split:

1. Create a new DuckDB file containing every control-plane table unchanged.
2. For the four big analytical tables, copy only rows with
   ``trade_date >= cutoff_date`` into the new file.
3. Replace the old file with the verified slimmed one and discard the old file.

After this script runs, the control-plane DB holds only a hot window of the
big tables.  ``WarmLayerStore`` merges the Parquet history with the hot window
so that readers still see the complete time series.

Use with care: verify the warm release before replacing the control plane.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import create_engine

from app.repositories.warm_layer import WarmLayerStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "stock_master.duckdb"

# Tables that are exported to the warm Parquet layer.  Only the hot window is
# retained in the slimmed control-plane DB.
HOT_WINDOW_TABLES: tuple[tuple[str, str, str], ...] = (
    ("market", "daily_bar", "trade_date"),
    ("market", "adjustment_factor", "trade_date"),
    ("raw", "daily_bar", "trade_date"),
    ("factor", "factor_value", "trade_date"),
    ("raw", "corporate_action", "ex_date"),
    ("fundamental", "financial_statement", "report_date"),
    ("raw", "financial_payload", "report_date"),
)


def _missing_warm_tables(warm_root: Path, tables: tuple[tuple[str, str, str], ...]) -> list[str]:
    """Require a complete release chain with Parquet for every table to be slimmed."""
    store = WarmLayerStore(warm_root=warm_root)
    latest = store.latest_release_tag
    if latest is None:
        return [f"{schema}.{table} (no successful warm release)" for schema, table, _ in tables]
    lineage = store.release_lineage(latest)
    incomplete = [tag for tag in lineage if not store._release_is_complete(tag)]
    if incomplete:
        return [f"release chain contains incomplete release(s): {', '.join(incomplete)}"]
    missing: list[str] = []
    for schema, table, _ in tables:
        paths = store.table_globs(schema, table, latest)
        if not paths or not any(
            next(path.rglob("*.parquet"), None) is not None for path in paths
        ):
            missing.append(f"{schema}.{table} (no Parquet in release chain {latest})")
    return missing


def _verify_cold_coverage(
    db_path: Path,
    warm_root: Path,
    cutoff_date: date,
    tables: tuple[tuple[str, str, str], ...],
    *,
    release_tag: str | None = None,
) -> None:
    """Ensure every cold row, including its lineage fields, exists in warm storage."""
    if not tables:
        return
    store = WarmLayerStore(warm_root=warm_root)
    latest = release_tag or store.latest_release_tag
    if latest is None:
        raise ValueError("no successful warm release")

    warm = store.connect(release_tag=latest, attach_control=False)
    try:
        escaped_path = db_path.as_posix().replace("'", "''")
        warm.execute(f"ATTACH '{escaped_path}' AS source (READ_ONLY)")
        for schema, table, date_column in tables:
            qualified = f'"{schema}"."{table}"'
            cold_filter = f'"{date_column}" < ? OR "{date_column}" IS NULL'
            cold_count = warm.execute(
                f"SELECT count(*) FROM source.{qualified} WHERE {cold_filter}",
                [cutoff_date],
            ).fetchone()[0]
            if not cold_count:
                continue

            source_columns = [
                row[0]
                for row in warm.execute(f"DESCRIBE SELECT * FROM source.{qualified}").fetchall()
            ]
            warm_columns = [
                row[0] for row in warm.execute(f"DESCRIBE SELECT * FROM {qualified}").fetchall()
            ]
            if set(source_columns) != set(warm_columns):
                raise ValueError(
                    f"warm column set differs for {schema}.{table}: "
                    f"source_only={set(source_columns) - set(warm_columns)}, "
                    f"warm_only={set(warm_columns) - set(source_columns)}"
                )
            columns = ", ".join(f'"{column}"' for column in source_columns)
            missing = warm.execute(
                f"SELECT {columns} FROM source.{qualified} WHERE {cold_filter} "
                f"EXCEPT ALL SELECT {columns} FROM {qualified} LIMIT 1",
                [cutoff_date],
            ).fetchone()
            if missing is not None:
                raise ValueError(
                    f"warm release {latest} does not cover all {cold_count} "
                    f"cold rows of {schema}.{table} with matching values and lineage; "
                    "export the updated table before slimming"
                )
            logger.info("Warm coverage verified for %s.%s: %d cold rows", schema, table, cold_count)
    finally:
        warm.close()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007


def _table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    row = con.execute(
        """
        SELECT count(*)
        FROM information_schema.tables
        WHERE table_catalog = current_database()
          AND table_schema = ?
          AND table_name = ?
        """,
        [schema, table],
    ).fetchone()
    return bool(row[0])


def _copy_control_tables(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
) -> list[str]:
    """Copy every non-hot-window table from source to target, preserving schema."""
    schemas = source.execute(
        """
        SELECT DISTINCT table_schema
        FROM information_schema.tables
        WHERE table_catalog = current_database()
          AND table_type = 'BASE TABLE'
        ORDER BY table_schema
        """
    ).fetchall()
    copied: list[str] = []
    for (schema_name,) in schemas:
        target.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
        tables = source.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_catalog = current_database()
              AND table_schema = ?
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            [schema_name],
        ).fetchall()
        for (table_name,) in tables:
            full_name = f"{schema_name}.{table_name}"
            if (schema_name, table_name) in {(s, t) for s, t, _ in HOT_WINDOW_TABLES}:
                continue
            logger.info("Copying control table %s", full_name)
            target.execute(f"CREATE TABLE {full_name} AS SELECT * FROM source.{full_name}")
            _copy_defaults(source, target, schema_name, table_name)
            _copy_indexes(source, target, schema_name, table_name)
            copied.append(full_name)
    return copied


def _copy_sequences(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
) -> list[str]:
    """Copy sequence DDL from source to target."""
    existing = {
        r[0]
        for r in target.execute(
            "SELECT sequence_name FROM duckdb_sequences() "
            "WHERE database_name = current_database()"
        ).fetchall()
    }
    created: list[str] = []
    rows = source.execute(
        "SELECT sequence_name, sql FROM duckdb_sequences() "
        "WHERE database_name = current_database()"
    ).fetchall()
    for name, sql in rows:
        if name in existing:
            continue
        target.execute(sql)
        created.append(name)
    return created


def _restore_all_defaults(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
) -> None:
    """Retry defaults after sequences exist in the target database."""
    rows = source.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_catalog = current_database()
          AND table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name
        """
    ).fetchall()
    for schema, table in rows:
        if _table_exists(target, schema, table):
            _copy_defaults(source, target, schema, table)


def _copy_defaults(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
) -> None:
    """Recreate column defaults so ORM inserts with missing columns work."""
    full_name = f"{schema}.{table}"
    rows = source.execute(
        """
        SELECT column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = ?
          AND table_name = ?
          AND column_default IS NOT NULL
        """,
        [schema, table],
    ).fetchall()
    for column, default_expr in rows:
        try:
            target.execute(
                f"ALTER TABLE {full_name} ALTER COLUMN {column} SET DEFAULT {default_expr}"
            )
        except Exception as exc:
            logger.warning("Failed to set default on %s.%s: %s", full_name, column, exc)


def _copy_indexes(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
) -> None:
    """Recreate every source index so queries and upserts keep working.

    DuckDB stores explicit regular and unique indexes in ``duckdb_indexes()``;
    table UNIQUE constraints appear in ``duckdb_constraints()``.  Both must be
    copied because CTAS drops them.
    """
    full_name = f"{schema}.{table}"
    existing_indexes = {
        r[0]
        for r in target.execute(
            """
            SELECT index_name
            FROM duckdb_indexes()
            WHERE database_name = current_database()
              AND schema_name = ?
              AND table_name = ?
            """,
            [schema, table],
        ).fetchall()
    }

    # 1) Copy explicit indexes already present in the source database.
    index_rows = source.execute(
        """
        SELECT index_name, sql, is_primary
        FROM duckdb_indexes()
        WHERE schema_name = ?
          AND table_name = ?
        """,
        [schema, table],
    ).fetchall()
    for idx_name, sql, is_primary in index_rows:
        if is_primary:
            continue
        if idx_name in existing_indexes:
            continue
        target.execute(sql)
        existing_indexes.add(idx_name)
        logger.info("Copied index %s on %s", idx_name, full_name)

    # 2) Also create indexes for any UNIQUE constraints that are not backed by
    #    an index in the source (rare, but keeps the previous behaviour).
    constraint_rows = source.execute(
        """
        SELECT constraint_column_names
        FROM duckdb_constraints()
        WHERE schema_name = ?
          AND table_name = ?
          AND constraint_type = 'UNIQUE'
        """,
        [schema, table],
    ).fetchall()
    for (cols,) in constraint_rows:
        if not cols:
            continue
        idx_name = f"ix_slim_{schema}_{table}_{'_'.join(cols)}"
        if idx_name in existing_indexes:
            continue
        col_sql = ", ".join(cols)
        target.execute(f"CREATE UNIQUE INDEX {idx_name} ON {full_name} ({col_sql})")
        existing_indexes.add(idx_name)
        logger.info("Created unique index %s on %s", idx_name, full_name)


def _copy_hot_window(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
    cutoff_date: date,
) -> list[dict[str, Any]]:
    """Copy only the hot-window slice of the big tables."""
    copied: list[dict[str, Any]] = []
    for schema, table, date_col in HOT_WINDOW_TABLES:
        full_name = f"{schema}.{table}"
        if not _table_exists(source, schema, table):
            logger.warning("Skipping missing table %s", full_name)
            continue
        before = source.execute(
            f"SELECT count(*), min({date_col}), max({date_col}) FROM {full_name}"
        ).fetchone()
        target.execute(
            f"""
            CREATE TABLE {full_name} AS
            SELECT *
            FROM source.{full_name}
            WHERE {date_col} >= '{cutoff_date}'
            """
        )
        _copy_defaults(source, target, schema, table)
        _copy_indexes(source, target, schema, table)
        after = target.execute(
            f"SELECT count(*), min({date_col}), max({date_col}) FROM {full_name}"
        ).fetchone()
        copied.append(
            {
                "table": full_name,
                "before_rows": int(before[0]),
                "before_min_date": str(before[1]) if before[1] else None,
                "before_max_date": str(before[2]) if before[2] else None,
                "after_rows": int(after[0]),
                "after_min_date": str(after[1]) if after[1] else None,
                "after_max_date": str(after[2]) if after[2] else None,
            }
        )
        logger.info(
            "Hot-window copied %s: %s -> %s rows",
            full_name,
            before[0],
            after[0],
        )
    return copied


def _verify_slimmed(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
    cutoff_date: date,
) -> None:
    """Reject a rebuild that lost tables, rows, indexes, sequences or defaults."""
    table_sql = (
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_catalog = current_database() AND table_type = 'BASE TABLE' "
        "AND table_schema NOT IN ('information_schema', 'pg_catalog')"
    )
    source_tables = set(source.execute(table_sql).fetchall())
    target_tables = set(target.execute(table_sql).fetchall())
    if source_tables != target_tables:
        raise ValueError(
            f"slimmed table set differs: missing={source_tables - target_tables}, "
            f"extra={target_tables - source_tables}"
        )

    hot_dates = {(schema, table): column for schema, table, column in HOT_WINDOW_TABLES}
    for schema, table in sorted(source_tables):
        qualified = f'"{schema}"."{table}"'
        date_column = hot_dates.get((schema, table))
        if date_column:
            expected = source.execute(
                f'SELECT count(*) FROM {qualified} WHERE "{date_column}" >= ?',
                [cutoff_date],
            ).fetchone()[0]
        else:
            expected = source.execute(f"SELECT count(*) FROM {qualified}").fetchone()[0]
        actual = target.execute(f"SELECT count(*) FROM {qualified}").fetchone()[0]
        if actual != expected:
            raise ValueError(
                f"slimmed row count differs for {schema}.{table}: "
                f"expected {expected}, got {actual}"
            )

    index_sql = (
        "SELECT schema_name, table_name, index_name, is_unique, sql "
        "FROM duckdb_indexes() WHERE database_name = current_database()"
    )
    source_indexes = set(source.execute(index_sql).fetchall())
    target_indexes = set(target.execute(index_sql).fetchall())
    if not source_indexes <= target_indexes:
        raise ValueError(f"slimmed indexes missing: {source_indexes - target_indexes}")

    sequence_sql = (
        "SELECT sequence_name FROM duckdb_sequences() "
        "WHERE database_name = current_database()"
    )
    source_sequences = set(source.execute(sequence_sql).fetchall())
    target_sequences = set(target.execute(sequence_sql).fetchall())
    if not source_sequences <= target_sequences:
        raise ValueError(f"slimmed sequences missing: {source_sequences - target_sequences}")

    default_sql = (
        "SELECT table_schema, table_name, column_name, column_default "
        "FROM information_schema.columns "
        "WHERE table_catalog = current_database() AND column_default IS NOT NULL"
    )
    source_defaults = set(source.execute(default_sql).fetchall())
    target_defaults = set(target.execute(default_sql).fetchall())
    if not source_defaults <= target_defaults:
        raise ValueError(f"slimmed column defaults missing: {source_defaults - target_defaults}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shrink the control-plane DuckDB to a hot window.")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to the control-plane DuckDB file",
    )
    parser.add_argument(
        "--cutoff-date",
        type=_parse_date,
        help="Retain big-table rows with trade_date >= this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=90,
        help="If --cutoff-date is omitted, retain the last N calendar days",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report sizes but do not create or replace the slimmed DB",
    )
    return parser


def _checkpoint_required(engine) -> None:
    """Fold schema changes into the file; failure must abort the swap."""
    with engine.connect() as conn:
        conn.exec_driver_sql("CHECKPOINT")
        conn.commit()


def _run(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        return 1

    wal = db_path.with_suffix(".duckdb.wal")
    if wal.exists():
        logger.error("WAL exists; stop writers and checkpoint the database first: %s", wal)
        return 1

    candidate = db_path.with_suffix(".duckdb.slimming")
    for stale in (candidate, Path(f"{candidate}.wal"), db_path.with_suffix(".duckdb.prev")):
        if stale.exists():
            logger.error("Previous slimming artifact exists; inspect it first: %s", stale)
            return 1

    cutoff_date = args.cutoff_date or (date.today() - timedelta(days=args.retention_days))
    logger.info("Cutoff date for hot window: %s", cutoff_date)

    source_con = duckdb.connect(str(db_path), read_only=True)
    slim_path: Path | None = None
    published = False
    try:
        original_size = db_path.stat().st_size
        logger.info("Original DB size: %.2f MiB", original_size / 1024 / 1024)

        # A partial release may inherit a table from several ancestors, while
        # an interrupted export may have a newer directory but no valid data.
        present_hot_tables = tuple(
            (schema, table, date_col)
            for schema, table, date_col in HOT_WINDOW_TABLES
            if _table_exists(source_con, schema, table)
        )
        missing_warm = _missing_warm_tables(BACKEND_DIR / "data" / "warm", present_hot_tables)
        if missing_warm:
            logger.error("Warm layer missing for tables: %s", missing_warm)
            return 1

        try:
            _verify_cold_coverage(
                db_path,
                BACKEND_DIR / "data" / "warm",
                cutoff_date,
                present_hot_tables,
            )
        except (duckdb.Error, ValueError) as exc:
            logger.error("Refusing to slim: %s", exc)
            return 1

        if args.dry_run:
            logger.info("Dry run: would retain rows >= %s", cutoff_date)
            for schema, table, date_col in HOT_WINDOW_TABLES:
                full_name = f"{schema}.{table}"
                if not _table_exists(source_con, schema, table):
                    continue
                row = source_con.execute(
                    f"""
                    SELECT count(*)
                    FROM {full_name}
                    WHERE {date_col} >= '{cutoff_date}'
                    """
                ).fetchone()
                logger.info("  %s hot-window rows: %s", full_name, row[0])
            return 0

        # Build slimmed DB next to the original, then atomically replace.
        slim_path = candidate

        target_con = duckdb.connect(str(slim_path))
        try:
            target_con.execute(f"ATTACH '{db_path}' AS source (READ_ONLY)")
            _copy_control_tables(source_con, target_con)
            sequences = _copy_sequences(source_con, target_con)
            if sequences:
                logger.info("Copied %s sequence(s)", len(sequences))
            _restore_all_defaults(source_con, target_con)
            hot_window_stats = _copy_hot_window(source_con, target_con, cutoff_date)
            _verify_slimmed(source_con, target_con, cutoff_date)
        finally:
            target_con.close()

        # Replace original with slimmed file.
        source_con.close()
        slim_size = slim_path.stat().st_size
        swapped = db_path.with_suffix(".duckdb.prev")
        db_path.rename(swapped)
        try:
            slim_path.rename(db_path)
            published = True
        except Exception:
            swapped.rename(db_path)
            raise
        # CTAS drops model id sequences/defaults. Recreate them above each
        # retained table maximum before the slimmed database is handed back to
        # the application.
        try:
            from app.database.session import ensure_duckdb_sequences

            repaired_engine = create_engine(f"duckdb:///{db_path.as_posix()}")
            try:
                ensure_duckdb_sequences(repaired_engine)
                # A failed checkpoint must block publication; the generic
                # startup helper only logs failures and is not sufficient here.
                _checkpoint_required(repaired_engine)
            finally:
                repaired_engine.dispose(close=True)
        except Exception:
            db_path.unlink()
            published_wal = Path(f"{db_path}.wal")
            if published_wal.exists():
                published_wal.unlink()
            swapped.rename(db_path)
            raise
        swapped.unlink()

        summary = {
            "original_db": str(db_path),
            "cutoff_date": cutoff_date.isoformat(),
            "original_size_bytes": original_size,
            "slim_size_bytes": slim_size,
            "size_reduction_bytes": original_size - slim_size,
            "size_reduction_pct": (
                (original_size - slim_size) / original_size * 100 if original_size else 0
            ),
            "hot_window_tables": hot_window_stats,
        }
        summary_path = (
            db_path.parent / f"slim_summary-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
        )
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Slimmed DB size: %.2f MiB", slim_size / 1024 / 1024)
        logger.info(
            "Reduction: %.2f MiB (%.1f%%)",
            summary["size_reduction_bytes"] / 1024 / 1024,
            summary["size_reduction_pct"],
        )
        logger.info("Summary written to %s", summary_path)
        return 0
    finally:
        source_con.close()
        if slim_path is not None and not published:
            for unpublished in (slim_path, Path(f"{slim_path}.wal")):
                if unpublished.exists():
                    unpublished.unlink()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run:
        return _run(args)

    from app.utils.duckdb_write_lock import (
        DuckDbWriteLockTimeout,
        duckdb_write_lock,
    )

    lock_path = Path(args.db_path).resolve().parent / "duckdb_write.lock"
    try:
        with duckdb_write_lock(lock_path, timeout_seconds=0):
            return _run(args)
    except DuckDbWriteLockTimeout as exc:
        logger.error("Slimming requires the exclusive database writer lock: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
