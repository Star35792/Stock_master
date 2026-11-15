"""Deduplicate ``lake.release`` and restore its unique index on ``release_id``.

Why this script exists
----------------------
``lake.release`` is the control-plane registry of every published dataset
version.  The ORM declares ``UniqueConstraint("release_id",
name="uq_release_release_id")`` on it, but DuckDB only materialises a
``UNIQUE`` constraint as an index when the table is created with
``CREATE TABLE ... UNIQUE``.  Every CTAS-based rebuild in this repository
therefore dropped it, and ``scripts.maintenance.restore_indexes`` cannot put
it back: that script reads ``table.indexes``, while a ``UniqueConstraint``
lives in ``table.constraints``.

The consequence is not theoretical.  With no unique index the table's
``ON CONFLICT`` upserts stop deduplicating, and four ``release_id`` values
were each registered twice -- 22 rows for 18 distinct releases.  The
duplicates are exact re-registrations: every column except the surrogate
``id`` and ``created_at`` matches, so they carry no information the surviving
row does not.

Fix order (the order matters)
-----------------------------
1. **Deduplicate first.**  ``CREATE UNIQUE INDEX`` fails outright with
   ``Data contains duplicates on indexed column(s)``, so the rows have to go
   before the index can be created.
2. **Then create the index.**  Only afterwards does ``ON CONFLICT`` regain
   its deduplication guarantee, and only then can a re-registration be
   rejected instead of silently inserted.

Safety
------
* The script keeps the **earliest** row of each duplicate group.  A release
  that was activated must not lose its row, and the surviving row is the one
  whose ``created_at`` records the original publication.
* Rows whose non-``id``/``created_at`` columns actually differ are **never**
  deleted: the script reports them and refuses to touch them, because that
  is a data conflict a human has to resolve, not a duplicate to collapse.
* Nothing outside ``lake.release`` is written.
* The whole run holds the DuckDB write lock, so it cannot race an ingest.

Usage
-----
    python -m scripts.maintenance.dedupe_lake_release --dry-run
    python -m scripts.maintenance.dedupe_lake_release
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = BACKEND_DIR / "data" / "stock_master.duckdb"
TABLE = "lake.release"
UNIQUE_INDEX = "uq_release_release_id"
UNIQUE_INDEX_COLUMNS = ("release_id",)


def _duplicate_groups(con) -> list[tuple[str, list[int]]]:
    """Return ``(release_id, [row ids])`` for every duplicated release."""
    rows = con.execute(
        f"""
        SELECT release_id, list(id ORDER BY created_at, id)
        FROM {TABLE}
        GROUP BY release_id
        HAVING count(*) > 1
        ORDER BY release_id
        """
    ).fetchall()
    return [(str(rid), [int(i) for i in ids]) for rid, ids in rows]


def _conflicting_columns(con, release_id: str, keep_id: int) -> list[str]:
    """Return the columns that differ between the kept row and its siblings."""
    rows = con.execute(
        f"SELECT * FROM {TABLE} WHERE release_id = ?", [release_id]
    ).fetchall()
    columns = [d[0] for d in con.execute(f"SELECT * FROM {TABLE} LIMIT 0").description]
    keep = next((dict(zip(columns, r, strict=True)) for r in rows if int(r[0]) == keep_id), None)
    if keep is None:
        return list(columns)
    differing: list[str] = []
    for column in columns:
        if column in ("id", "created_at"):
            continue
        for row in rows:
            if int(row[0]) == keep_id:
                continue
            if dict(zip(columns, row, strict=True))[column] != keep[column]:
                differing.append(column)
                break
    return differing


def _ensure_unique_index(con) -> bool:
    """Create the unique index on ``release_id``; return True when present."""
    existing = con.execute(
        "SELECT index_name, is_unique FROM duckdb_indexes() WHERE table_name = 'release'"
    ).fetchall()
    for name, is_unique in existing:
        if is_unique and name == UNIQUE_INDEX:
            return True
    con.execute(
        f"CREATE UNIQUE INDEX {UNIQUE_INDEX} ON {TABLE} (release_id)"
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path).resolve()
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        return 1

    with duckdb_write_lock(str(db_path.parent / "duckdb_write.lock")):
        con = duckdb.connect(str(db_path))
        try:
            total_before = con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
            groups = _duplicate_groups(con)
            logger.info("Rows before: %d", total_before)
            logger.info("Duplicate release_id groups: %d", len(groups))

            to_delete: list[int] = []
            blocked: list[tuple[str, list[str]]] = []
            for release_id, ids in groups:
                keep_id = ids[0]  # earliest created_at, then lowest id
                differing = _conflicting_columns(con, release_id, keep_id)
                if differing:
                    blocked.append((release_id, differing))
                    logger.warning(
                        "Refusing to collapse %s: rows differ in %s",
                        release_id,
                        ", ".join(differing),
                    )
                    continue
                to_delete.extend(ids[1:])
                logger.info(
                    "  %s: keep id=%d, remove id=%s",
                    release_id,
                    keep_id,
                    ",".join(str(i) for i in ids[1:]),
                )

            if blocked:
                logger.error(
                    "%d duplicate group(s) carry conflicting data and were left "
                    "alone. Resolve them by hand before retrying.",
                    len(blocked),
                )
                return 1

            if args.dry_run:
                logger.info("Dry run: %d row(s) would be removed", len(to_delete))
                return 0

            if to_delete:
                placeholders = ",".join("?" for _ in to_delete)
                con.execute(f"DELETE FROM {TABLE} WHERE id IN ({placeholders})", to_delete)
                con.execute("CHECKPOINT")
                logger.info("Removed %d duplicate row(s)", len(to_delete))

            _ensure_unique_index(con)
            con.execute("CHECKPOINT")
            logger.info("Unique index %s is in place", UNIQUE_INDEX)

            total_after = con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
            distinct = con.execute(
                f"SELECT count(DISTINCT release_id) FROM {TABLE}"
            ).fetchone()[0]
            logger.info("Rows after: %d (distinct release_id: %d)", total_after, distinct)
            if total_after != distinct:
                logger.error("Duplicates remain: %d rows / %d releases", total_after, distinct)
                return 1
        finally:
            con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
