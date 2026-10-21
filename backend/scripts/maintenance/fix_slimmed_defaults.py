"""Recreate column defaults lost during the first ``slim_metadata_db.py`` run.

``CREATE TABLE ... AS SELECT`` does not preserve ``server_default`` values.
This script reads ``information_schema.columns.column_default`` from a source
DB and applies the same defaults to the target DB.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "stock_master.duckdb"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recreate column defaults in slimmed DB")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--source-backup", help="Source DB to read defaults from")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        return 1

    source = Path(args.source_backup) if args.source_backup else db_path
    if not source.exists():
        logger.error("Source DB not found: %s", source)
        return 1

    src_con = duckdb.connect(str(source), read_only=True)
    tgt_con = duckdb.connect(str(db_path))
    try:
        rows = src_con.execute(
            """
            SELECT table_schema, table_name, column_name, column_default
            FROM information_schema.columns
            WHERE column_default IS NOT NULL
            ORDER BY table_schema, table_name, ordinal_position
            """
        ).fetchall()
        for schema, table, column, default_expr in rows:
            full_name = f"{schema}.{table}"
            try:
                tgt_con.execute(
                    f"ALTER TABLE {full_name} ALTER COLUMN {column} SET DEFAULT {default_expr}"
                )
                logger.info("Set default on %s.%s = %s", full_name, column, default_expr)
            except Exception as exc:
                logger.warning("Failed to set default on %s.%s: %s", full_name, column, exc)
    finally:
        src_con.close()
        tgt_con.close()

    logger.info("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
