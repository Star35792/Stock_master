"""Create missing primary-key and unique constraints in the slimmed control-plane DB.

The first version of ``slim_metadata_db.py`` copied tables but not constraints.
This script reconstructs PRIMARY KEY and UNIQUE constraints from a source DB
(usually the pre-slim backup) so that SQLAlchemy upserts work again.
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
    parser = argparse.ArgumentParser(description="Create missing PK/unique constraints")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--source-backup", help="Source DB to read constraints from")
    return parser


def _add_constraints(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
) -> None:
    rows = source.execute(
        """
        SELECT schema_name, table_name, constraint_type, constraint_column_names, expression
        FROM duckdb_constraints()
        WHERE constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'CHECK', 'NOT NULL')
        ORDER BY schema_name, table_name, constraint_type
        """
    ).fetchall()
    for schema, table, ctype, cols, expr in rows:
        full = f"{schema}.{table}"
        try:
            if ctype == "PRIMARY KEY":
                target.execute(f"ALTER TABLE {full} ADD PRIMARY KEY ({', '.join(cols)})")
                logger.info("Added PK on %s (%s)", full, ", ".join(cols))
            elif ctype == "UNIQUE":
                # DuckDB does not support ALTER TABLE ADD UNIQUE; use a unique
                # index so SQLAlchemy's ON CONFLICT can target it.
                idx_name = f"ix_slim_{schema}_{table}_{'_'.join(cols)}"
                target.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} ON {full} ({', '.join(cols)})"
                )
                logger.info("Added unique index %s on %s (%s)", idx_name, full, ", ".join(cols))
            elif ctype == "CHECK":
                target.execute(f"ALTER TABLE {full} ADD CHECK ({expr})")
                logger.info("Added CHECK on %s", full)
            elif ctype == "NOT NULL":
                for col in cols:
                    target.execute(f"ALTER TABLE {full} ALTER COLUMN {col} SET NOT NULL")
                logger.info("Added NOT NULL on %s (%s)", full, ", ".join(cols))
        except Exception as exc:
            logger.warning("Constraint already exists or failed on %s: %s", full, exc)


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
        _add_constraints(src_con, tgt_con)
    finally:
        src_con.close()
        tgt_con.close()

    logger.info("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
