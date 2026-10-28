"""Shrink the control-plane DuckDB by off-loading historical rows to Parquet.

This is Phase 2B of the storage split:

1. Back up the current ``stock_master.duckdb``.
2. Create a new DuckDB file containing every control-plane table unchanged.
3. For the four big analytical tables, copy only rows with
   ``trade_date >= cutoff_date`` into the new file.
4. Atomically replace the old file with the slimmed one.

After this script runs, the control-plane DB holds only a hot window of the
big tables.  ``WarmLayerStore`` merges the Parquet history with the hot window
so that readers still see the complete time series.

Use with care: keep the backup until you have verified ``daily_loop`` and
backtests still work.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import create_engine

from app.database.session import ensure_duckdb_sequences

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
            if (schema_name, table_name) in {
                (s, t) for s, t, _ in HOT_WINDOW_TABLES
            }:
                continue
            logger.info("Copying control table %s", full_name)
            target.execute(f"CREATE TABLE {full_name} AS SELECT * FROM source.{full_name}")
            _copy_defaults(source, target, schema_name, table_name)
            _copy_unique_indexes(source, target, schema_name, table_name)
            copied.append(full_name)
    return copied


def _copy_sequences(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
) -> list[str]:
    """Copy sequence DDL from source to target."""
    existing = {
        r[0]
        for r in target.execute("SELECT sequence_name FROM duckdb_sequences()").fetchall()
    }
    created: list[str] = []
    rows = source.execute("SELECT sequence_name, sql FROM duckdb_sequences()").fetchall()
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
            logger.warning(
                "Failed to set default on %s.%s: %s", full_name, column, exc
            )


def _copy_unique_indexes(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
) -> None:
    """Recreate unique indexes so upsert ``ON CONFLICT`` targets keep working.

    DuckDB stores unique constraints from ``CREATE UNIQUE INDEX`` only in
    ``duckdb_indexes()``, while ``PRIMARY KEY`` appears in
    ``duckdb_constraints()``.  We copy both the actual unique indexes (via their
    SQL) and any standalone UNIQUE constraints by building an equivalent index.
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

    # 1) Copy unique indexes already present in the source database.
    index_rows = source.execute(
        """
        SELECT index_name, sql, is_primary
        FROM duckdb_indexes()
        WHERE schema_name = ?
          AND table_name = ?
          AND is_unique = true
        """,
        [schema, table],
    ).fetchall()
    for idx_name, sql, is_primary in index_rows:
        if is_primary:
            continue
        if idx_name in existing_indexes:
            continue
        try:
            target.execute(sql)
            logger.info("Copied unique index %s on %s", idx_name, full_name)
        except Exception as exc:
            logger.warning(
                "Failed to copy unique index %s on %s: %s", idx_name, full_name, exc
            )

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
        try:
            target.execute(f"CREATE UNIQUE INDEX {idx_name} ON {full_name} ({col_sql})")
            logger.info("Created unique index %s on %s", idx_name, full_name)
        except Exception as exc:
            logger.warning(
                "Failed to create unique index on %s (%s): %s", full_name, col_sql, exc
            )


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
        _copy_unique_indexes(source, target, schema, table)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Shrink the control-plane DuckDB to a hot window."
    )
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

    cutoff_date = args.cutoff_date or (date.today() - timedelta(days=args.retention_days))
    logger.info("Cutoff date for hot window: %s", cutoff_date)

    source_con = duckdb.connect(str(db_path), read_only=True)
    try:
        original_size = db_path.stat().st_size
        logger.info("Original DB size: %.2f MiB", original_size / 1024 / 1024)

        # Verify warm layer exists for every hot-window table.
        missing_warm: list[str] = []
        for schema, table, _ in HOT_WINDOW_TABLES:
            if not _table_exists(source_con, schema, table):
                continue
            # The WarmLayerStore release path is data/warm/exported/<tag>/<schema>/<table>
            warm_root = BACKEND_DIR / "data" / "warm" / "exported"
            if not warm_root.exists():
                missing_warm.append(f"{schema}.{table} (no warm root)")
                continue
            releases = sorted(warm_root.iterdir())
            if not releases:
                missing_warm.append(f"{schema}.{table} (no releases)")
                continue
            latest = releases[-1]
            parquet_dir = latest / schema / table
            if not parquet_dir.exists():
                missing_warm.append(f"{schema}.{table} (no parquet dir {parquet_dir})")
        if missing_warm:
            logger.error("Warm layer missing for tables: %s", missing_warm)
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

        # Backup
        backup_path: Path | None = None
        if not args.skip_backup:
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"{db_path.stem}-pre-slim-{stamp}{db_path.suffix}"
            shutil.copy2(db_path, backup_path)
            logger.info("Backup created: %s", backup_path)

        # Build slimmed DB next to the original, then atomically replace.
        slim_path = db_path.with_suffix(".duckdb.slimming")
        if slim_path.exists():
            slim_path.unlink()

        target_con = duckdb.connect(str(slim_path))
        try:
            target_con.execute(f"ATTACH '{db_path}' AS source (READ_ONLY)")
            _copy_control_tables(source_con, target_con)
            sequences = _copy_sequences(source_con, target_con)
            if sequences:
                logger.info("Copied %s sequence(s)", len(sequences))
            _restore_all_defaults(source_con, target_con)
            hot_window_stats = _copy_hot_window(source_con, target_con, cutoff_date)
        finally:
            target_con.close()

        # Replace original with slimmed file.
        slim_size = slim_path.stat().st_size
        swapped = db_path.with_suffix(".duckdb.prev")
        db_path.rename(swapped)
        slim_path.rename(db_path)
        swapped.unlink()

        # CTAS drops model id sequences/defaults. Recreate them above each
        # retained table maximum before the slimmed database is handed back to
        # the application.
        repaired_engine = create_engine(f"duckdb:///{db_path.as_posix()}")
        try:
            ensure_duckdb_sequences(repaired_engine)
        finally:
            repaired_engine.dispose(close=True)

        summary = {
            "original_db": str(db_path),
            "backup_db": str(backup_path) if backup_path else None,
            "cutoff_date": cutoff_date.isoformat(),
            "original_size_bytes": original_size,
            "slim_size_bytes": slim_size,
            "size_reduction_bytes": original_size - slim_size,
            "size_reduction_pct": (
                (original_size - slim_size) / original_size * 100
                if original_size else 0
            ),
            "hot_window_tables": hot_window_stats,
        }
        summary_path = (
            db_path.parent
            / f"slim_summary-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
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


if __name__ == "__main__":
    sys.exit(main())
