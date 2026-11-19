"""Check indexed lookups against physical rows and repair proven stale indexes.

Run from backend/. The default is read-only. Applying a repair rebuilds only
explicit single-column unique indexes whose lookups disagree with a full scan.
Rows are never copied, updated or deleted. A temporary equivalent unique index
protects the table while DuckDB releases and recreates the original index name.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402

TARGET_TABLES = ("metadata.job_log", "market.security")
MAX_PROBE_KEYS = 10_000
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


@dataclass(frozen=True)
class IndexSpec:
    schema: str
    table: str
    name: str
    unique: bool
    columns: tuple[str, ...]
    sql: str

    @property
    def table_name(self) -> str:
        return f"{self.schema}.{self.table}"


class IndexRepairError(RuntimeError):
    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def quote(name: str) -> str:
    if not IDENTIFIER.fullmatch(name):
        raise ValueError(f"unsupported identifier: {name!r}")
    return f'"{name}"'


def qualified(table: str) -> str:
    schema, name = table.split(".", 1)
    return f"{quote(schema)}.{quote(name)}"


def inventory(connection: Any, tables: tuple[str, ...]) -> list[IndexSpec]:
    rows = connection.execute(
        "SELECT schema_name,table_name,index_name,is_unique,expressions,sql "
        "FROM duckdb_indexes() WHERE database_name=current_database() "
        "ORDER BY schema_name,table_name,index_name"
    ).fetchall()
    result = []
    for schema, table, name, unique, expressions, sql in rows:
        if f"{schema}.{table}" not in tables:
            continue
        columns = tuple(
            part.strip().strip("'").strip('"')
            for part in expressions.strip("[]").split(",")
        )
        if not all(IDENTIFIER.fullmatch(column) for column in columns):
            continue
        result.append(IndexSpec(schema, table, name, unique, columns, sql))
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"unsupported database value: {type(value).__name__}")


def fingerprint(connection: Any, table: str) -> dict[str, Any]:
    """Hash every column in physical row order, independent of index filtering."""
    digest = sha256()
    count = 0
    connection.execute("PRAGMA disable_optimizer")
    try:
        cursor = connection.execute(f"SELECT * FROM {qualified(table)} ORDER BY rowid")
        columns = [column[0] for column in cursor.description]
        digest.update(json.dumps(columns, ensure_ascii=True).encode())
        while rows := cursor.fetchmany(1024):
            for row in rows:
                encoded = json.dumps(
                    row, ensure_ascii=True, separators=(",", ":"), default=_json_value,
                ).encode()
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
                count += 1
    finally:
        connection.execute("PRAGMA enable_optimizer")
    return {"rows": count, "sha256": digest.hexdigest(), "columns": columns}


def lookup_count(connection: Any, spec: IndexSpec, key: Any) -> int:
    rows = connection.execute(
        f"SELECT {quote(spec.columns[0])} FROM {qualified(spec.table_name)} "
        f"WHERE {quote(spec.columns[0])}=?", [key],
    ).fetchall()
    return len(rows)


def probe_index(connection: Any, spec: IndexSpec) -> dict[str, Any]:
    if not spec.unique or len(spec.columns) != 1:
        return {"verified": False, "reason": "requires_single_column_unique_index"}
    column = quote(spec.columns[0])
    connection.execute("PRAGMA disable_optimizer")
    try:
        keys = connection.execute(
            f"SELECT {column},count(*) FROM {qualified(spec.table_name)} "
            f"GROUP BY {column} ORDER BY {column} LIMIT {MAX_PROBE_KEYS + 1}"
        ).fetchall()
    finally:
        connection.execute("PRAGMA enable_optimizer")
    if len(keys) > MAX_PROBE_KEYS:
        return {"verified": False, "reason": "probe_key_limit_exceeded"}
    invalid = [key for key, count in keys if key is None or count != 1]
    if invalid:
        return {"verified": False, "reason": "null_or_duplicate_keys", "invalid_keys": invalid[:20]}
    mismatches = [key for key, count in keys if lookup_count(connection, spec, key) != count]
    return {
        "verified": True, "keys_checked": len(keys), "mismatch_count": len(mismatches),
        "mismatch_keys": mismatches[:20],
    }


def audit(
    connection: Any, tables: tuple[str, ...] = TARGET_TABLES,
) -> dict[str, Any]:
    specs = inventory(connection, tables)
    return {
        "tables": {table: fingerprint(connection, table) for table in tables},
        "indexes": [{**asdict(spec), "probe": probe_index(connection, spec)} for spec in specs],
    }


def _create_guard(connection: Any, spec: IndexSpec, name: str) -> None:
    columns = ",".join(quote(column) for column in spec.columns)
    connection.execute(
        f"CREATE UNIQUE INDEX {quote(name)} ON {qualified(spec.table_name)} ({columns})"
    )


def _drop(connection: Any, spec: IndexSpec, name: str) -> None:
    connection.execute(f"DROP INDEX {quote(spec.schema)}.{quote(name)}")


def rebuild_indexes(connection: Any, names: list[str]) -> dict[str, Any]:
    """Caller owns the writer lock and connection; each DDL autocommits.

    DuckDB cannot DROP and CREATE the same index name in one transaction. The
    equivalent guard is committed first and retained on an interrupted repair.
    """
    if not names:
        raise ValueError("select at least one proven inconsistent index")
    before = audit(connection)
    specs = {spec.name: spec for spec in inventory(connection, TARGET_TABLES)}
    probes = {item["name"]: item["probe"] for item in before["indexes"]}
    selected = []
    for name in dict.fromkeys(names):
        probe = probes.get(name, {})
        if name not in specs or not probe.get("verified") or not probe.get("mismatch_count"):
            raise ValueError(f"no proven lookup inconsistency for index {name!r}; no repair")
        selected.append(specs[name])
    all_names = {row[0] for row in connection.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE database_name=current_database()"
    ).fetchall()}
    for spec in selected:
        if f"{spec.name}__repair_guard" in all_names:
            raise ValueError(f"existing repair guard for {spec.name!r}; inspect before resuming")

    repaired = []
    phase = "preflight_passed"
    current_index = None
    try:
        for spec in selected:
            current_index = spec.name
            guard = f"{spec.name}__repair_guard"
            _create_guard(connection, spec, guard)
            phase = "guard_created"
            _drop(connection, spec, spec.name)
            phase = "original_dropped"
            # If original-name recreation fails, the committed guard still enforces
            # uniqueness. Do not drop it or silently treat the repair as successful.
            connection.execute(spec.sql)
            phase = "original_recreated"
            _drop(connection, spec, guard)
            phase = "guard_dropped"
            repaired.append(spec.name)
    except Exception as exc:
        failure = {
            "before": before, "repaired_indexes": repaired,
            "current_index": current_index, "phase": phase,
        }
        try:
            failure["remaining_indexes"] = [
                asdict(item) for item in inventory(connection, TARGET_TABLES)
            ]
        except Exception as inventory_error:  # noqa: BLE001 - retain initial evidence
            failure["inventory_error"] = str(inventory_error)[:500]
        raise IndexRepairError(str(exc), failure) from exc

    try:
        after = audit(connection)
    except Exception as exc:
        raise IndexRepairError(str(exc), {
            "before": before, "repaired_indexes": repaired,
            "phase": "post_rebuild_verification",
        }) from exc
    completed = {"repaired_indexes": repaired, "before": before, "after": after}
    if before["tables"] != after["tables"]:
        raise IndexRepairError("index repair changed physical rows or columns", completed)
    after_probes = {item["name"]: item["probe"] for item in after["indexes"]}
    if any(after_probes.get(name, {}).get("mismatch_count") for name in repaired):
        raise IndexRepairError("index lookup inconsistency remains after rebuild", completed)
    if {item["name"] for item in before["indexes"]} != {
        item["name"] for item in after["indexes"]
    }:
        raise IndexRepairError("index inventory changed after rebuild", completed)
    return completed


def write_audit(connection: Any, report: dict[str, Any], operator: str, reason: str) -> int:
    now = datetime.now(UTC)
    row = connection.execute(
        "INSERT INTO metadata.job_log "
        "(job_name,job_type,status,started_at,finished_at,rows_read,metadata) "
        "VALUES ('duckdb_index_consistency_repair','repair','success',?,?,?,?) RETURNING id",
        [now, now, sum(item["rows"] for item in report["before"]["tables"].values()),
         json.dumps({"operator": operator, "reason": reason, **report}, default=_json_value)],
    ).fetchone()
    return int(row[0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(settings.metadata_db_path))
    parser.add_argument("--index", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    database = args.database.resolve()
    if not database.is_file():
        print(json.dumps({"error": "database_not_found", "database": str(database)}))
        return 1
    if args.apply and (
        not args.index or not (args.operator or "").strip() or not (args.reason or "").strip()
    ):
        print(json.dumps({"error": "apply_requires_index_operator_and_reason"}))
        return 1
    report: dict[str, Any] = {}
    try:
        if not args.apply:
            with duckdb.connect(str(database), read_only=True) as connection:
                report = audit(connection)
            print(json.dumps(report, ensure_ascii=False, default=_json_value))
            return 0
        with duckdb_write_lock(database.parent / "duckdb_write.lock", timeout_seconds=0):
            # The native writer connection also refuses another process holding
            # the file; do not fall back to copying or moving DB/WAL files.
            with duckdb.connect(str(database)) as connection:
                report = rebuild_indexes(connection, args.index)
                report["audit_job_id"] = write_audit(
                    connection, report, args.operator.strip(), args.reason.strip(),
                )
                after_audit = audit(connection)
                connection.execute("CHECKPOINT")
            with duckdb.connect(str(database), read_only=True) as verify:
                report["reopened"] = audit(verify)
                if after_audit != report["reopened"]:
                    raise RuntimeError("checkpoint/reopen changed rows or index lookups")
                if verify.execute(
                    "SELECT id FROM metadata.job_log WHERE id=?", [report["audit_job_id"]],
                ).fetchone() is None:
                    raise RuntimeError("repair audit is not readable by id after reopen")
            print(json.dumps(report, ensure_ascii=False, default=_json_value))
            return 0
    except Exception as exc:  # noqa: BLE001 - preserve a bounded failure report
        print(json.dumps({
            "error": type(exc).__name__, "detail": str(exc)[:2000],
            "repair": getattr(exc, "report", report),
        }, default=_json_value))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
