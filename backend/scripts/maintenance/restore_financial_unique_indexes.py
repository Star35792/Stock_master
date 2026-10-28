"""Audit ORM uniqueness database-wide; restore only two financial upsert indexes.

Run with the application stopped. No rows are deleted or deduplicated. The JSON
audit is written on success or failure; --apply requires the shared writer lock.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

import app.models  # noqa: F401 - register the complete ORM metadata
from app.database.base import Base
from app.utils.duckdb_write_lock import duckdb_write_lock

TARGETS = {
    "fundamental.financial_statement": "uq_financial_statement_key",
    "fundamental.financial_indicator": "uq_financial_indicator_key",
}
BACKEND = Path(__file__).resolve().parents[2]


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def audit(connection: duckdb.DuckDBPyConnection) -> dict:
    """Inventory all ORM PK/unique keys, including equivalent named indexes."""
    tables = set(
        connection.execute("SELECT schema_name, table_name FROM duckdb_tables()").fetchall()
    )
    keys = set()
    indexes = connection.execute(
        "SELECT schema_name, table_name, index_name, is_unique, expressions FROM duckdb_indexes()"
    ).fetchall()
    for schema, table, _, unique, expressions in indexes:
        if unique:
            keys.add((schema, table, tuple(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", expressions))))
    for schema, table, columns in connection.execute(
        "SELECT schema_name, table_name, constraint_column_names FROM duckdb_constraints() "
        "WHERE constraint_type IN ('PRIMARY KEY', 'UNIQUE')"
    ).fetchall():
        keys.add((schema, table, tuple(columns)))
    inventory = []
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.fullname):
        declared = [
            (index.name, tuple(c.name for c in index.columns))
            for index in table.indexes
            if index.unique
        ]
        declared += [
            (
                constraint.name or type(constraint).__name__,
                tuple(c.name for c in constraint.columns),
            )
            for constraint in table.constraints
            if isinstance(constraint, (PrimaryKeyConstraint, UniqueConstraint))
        ]
        for name, columns in sorted(declared):
            inventory.append(
                {
                    "table": table.fullname,
                    "name": name,
                    "columns": list(columns),
                    "table_exists": (table.schema, table.name) in tables,
                    "present": (table.schema, table.name, columns) in keys,
                }
            )
    target_checks = []
    for fullname, name in TARGETS.items():
        table = Base.metadata.tables[fullname]
        index = next(i for i in table.indexes if i.name == name)
        columns = tuple(c.name for c in index.columns)
        exists = (table.schema, table.name) in tables
        duplicates = null_rows = None
        if exists:
            quoted = f"{_quote(table.schema)}.{_quote(table.name)}"
            group = ", ".join(_quote(c) for c in columns)
            duplicates = connection.execute(
                f"SELECT count(*) FROM (SELECT {group} FROM {quoted} "
                f"GROUP BY {group} HAVING count(*) > 1)"
            ).fetchone()[0]
            where = " OR ".join(f"{_quote(c)} IS NULL" for c in columns)
            null_rows = connection.execute(
                f"SELECT count(*) FROM {quoted} WHERE {where}"
            ).fetchone()[0]
        target_checks.append(
            {
                "table": fullname,
                "index": name,
                "columns": list(columns),
                "table_exists": exists,
                "present": (table.schema, table.name, columns) in keys,
                "duplicate_groups": duplicates,
                "null_key_rows": null_rows,
            }
        )
    return {
        "uniqueness_inventory": inventory,
        "targets": target_checks,
        "existing_indexes": [list(row) for row in indexes],
    }


def restore(connection: duckdb.DuckDBPyConnection, before: dict) -> list[str]:
    """Atomic, allowlisted repair. Refuse ambiguous/malformed source data."""
    if any(
        not t["table_exists"] or t["duplicate_groups"] or t["null_key_rows"]
        for t in before["targets"]
    ):
        raise ValueError("financial keys missing, duplicated or null; no repair performed")
    created = []
    connection.execute("BEGIN TRANSACTION")
    try:
        for fullname, name in TARGETS.items():
            target = next(t for t in before["targets"] if t["table"] == fullname)
            if target["present"]:
                continue
            table = Base.metadata.tables[fullname]
            index = next(i for i in table.indexes if i.name == name)
            columns = ", ".join(_quote(c.name) for c in index.columns)
            connection.execute(
                f"CREATE UNIQUE INDEX {_quote(name)} ON "
                f"{_quote(table.schema)}.{_quote(table.name)} ({columns})"
            )
            created.append(name)
        after = audit(connection)
        if not all(t["present"] for t in after["targets"]):
            raise RuntimeError("post-repair financial uniqueness verification failed")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=BACKEND / "data/stock_master.duckdb")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--audit-path", type=Path)
    args = parser.parse_args()
    db_path = args.db_path.resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output = (
        args.audit_path
        or BACKEND / "outputs" / f"financial-index-audit-{stamp}-{uuid4().hex[:8]}.json"
    )
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "database": str(db_path),
        "apply": args.apply,
        "status": "failed",
    }
    try:
        if not db_path.is_file():
            raise FileNotFoundError(db_path)
        if args.apply:
            with duckdb_write_lock(db_path.parent / "duckdb_write.lock"):
                with duckdb.connect(str(db_path)) as connection:
                    report["before"] = audit(connection)
                    report["created_indexes"] = restore(connection, report["before"])
                    report["after"] = audit(connection)
        else:
            with duckdb.connect(str(db_path), read_only=True) as connection:
                report["before"] = audit(connection)
        report["status"] = "success"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
        print(f"status={report['status']} audit={output}")
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
