"""Create missing sequences in the slimmed control-plane DB.

The first version of ``slim_metadata_db.py`` copied tables but not sequences.
This script reconstructs every sequence from the pre-slim backup using the
``sql`` DDL stored in DuckDB's ``duckdb_sequences()`` catalog.
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
    parser = argparse.ArgumentParser(description="Create missing sequences in slimmed DB")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--source-backup", help="Optional source DB to read sequence DDL from")
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
    try:
        sequences = src_con.execute("SELECT sequence_name, sql FROM duckdb_sequences()").fetchall()
    finally:
        src_con.close()

    tgt_con = duckdb.connect(str(db_path))
    try:
        existing = {
            r[0] for r in tgt_con.execute("SELECT sequence_name FROM duckdb_sequences()").fetchall()
        }
        for name, sql in sequences:
            if name in existing:
                logger.info("Sequence %s already exists, skipping", name)
                continue
            logger.info("Creating sequence %s", name)
            tgt_con.execute(sql)
    finally:
        tgt_con.close()

    logger.info("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
