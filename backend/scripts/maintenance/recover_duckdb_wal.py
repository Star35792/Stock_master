"""Recover a DuckDB database whose WAL cannot be replayed.

DuckDB cannot replay a WAL entry that carries a default expression
(duckdb/duckdb#18259).  When a process is killed between such a DDL and the
next checkpoint, the database file itself stays intact but the database can no
longer be opened::

    INTERNAL Error: Failure while replaying WAL file "...wal": Calling
    DatabaseManager::GetDefaultDatabase with no default database set

The fix is not to delete the WAL -- that discards every transaction that was
committed but not yet checkpointed.  Instead, attach the file from an in-memory
database: the ATTACH provides the default database context the replay needs, so
the WAL replays normally and a checkpoint folds it into the database file.

The script never invents rows.  It replays what was already committed, writes
an audit record to the ``metadata`` domain, and refuses to run while another
process holds the database.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.utils.duckdb_write_lock import (  # noqa: E402
    DuckDbWriteLockTimeout,
    duckdb_write_lock,
)

DEFAULT_DATABASE = Path("data/stock_master.duckdb")
DEFAULT_LOCK = Path("data/duckdb_write.lock")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay an unreplayable DuckDB WAL via ATTACH and checkpoint it.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help="Path to the DuckDB database file.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("data/backups"),
        help="Directory that receives the pre-recovery copy of the database and WAL.",
    )
    parser.add_argument(
        "--lock-path",
        type=Path,
        default=DEFAULT_LOCK,
        help="Writer lock guarding the main database.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip the backup copy. Only safe when another copy already exists.",
    )
    return parser


def _classify_open_failure(path: Path) -> tuple[bool, str]:
    """Return (occupied, reason).

    A database that another process holds open cannot be recovered: ATTACH
    would hit a file-handle conflict, and copying the file would produce two
    slices taken at different instants, so the backup could not be rolled back.
    """
    try:
        con = duckdb.connect(str(path))
    except Exception as exc:  # noqa: BLE001 - report any open failure verbatim
        message = f"{type(exc).__name__}: {exc}"
        lowered = str(exc).lower()
        occupied = (
            "另一个程序正在使用此文件" in str(exc)
            or "file is already open" in lowered
            or "unique file handle conflict" in lowered
        )
        return occupied, message
    try:
        con.execute("SELECT count(*) FROM duckdb_tables()").fetchone()
    except Exception as exc:  # noqa: BLE001 - report any open failure verbatim
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        con.close()
    return False, ""


def _row_count(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> int:
    try:
        return int(con.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()[0])
    except Exception:  # noqa: BLE001 - a missing table is not a recovery failure
        return -1


def _write_audit(database: Path, wal_size: int, backups: list[Path]) -> None:
    """Record the recovery in the ``metadata`` domain.

    AGENT.MD requires that a manual repair is auditable, so the recovery is
    written as a job-log entry rather than only printed to stdout.
    """
    try:
        con = duckdb.connect(str(database))
    except Exception:  # noqa: BLE001 - auditing must never mask a recovery
        return
    try:
        con.execute(
            """
            INSERT INTO metadata.job_log
                (job_name, job_type, status, rows_read, rows_inserted, error_message, metadata)
            VALUES (?, 'repair', 'success', ?, ?, ?, ?)
            """,
            [
                "duckdb_wal_recovery",
                wal_size,
                len(backups),
                "replayed an unreplayable DuckDB WAL via ATTACH (duckdb#18259)",
                {
                    "database": str(database),
                    "wal_size_bytes": wal_size,
                    "backups": [str(path) for path in backups],
                    "recovered_at": datetime.now(UTC).isoformat(),
                },
            ],
        )
    except Exception:  # noqa: BLE001 - auditing must never mask a recovery
        return
    finally:
        con.close()


def main() -> int:
    args = build_parser().parse_args()
    database = args.database.resolve()
    wal = database.with_name(database.name + ".wal")

    if not database.exists():
        print(f"recover.error=database_not_found path={database}")
        return 1

    occupied, reason = _classify_open_failure(database)
    if occupied:
        print(f"recover.error=database_locked {reason}")
        print("recover.hint=stop every API/sync process that holds the database, then retry")
        return 1
    if not reason:
        print("recover.status=already_openable")
        print("recover.hint=no recovery needed; the database opens without a WAL replay")
        return 0

    print(f"recover.direct_open_failed={reason}")

    if not wal.exists():
        print("recover.error=no_wal_present")
        print("recover.hint=the failure is not an unreplayable WAL; inspect the error above")
        return 1

    try:
        lock = duckdb_write_lock(args.lock_path, timeout_seconds=0)
    except Exception as exc:  # noqa: BLE001 - lock setup failures are reportable
        print(f"recover.error=lock_setup_failed {type(exc).__name__}: {exc}")
        return 1

    try:
        with lock:
            # Another writer may have taken the database between the probe and
            # the lock, so re-check while the lock is held.
            occupied, reason = _classify_open_failure(database)
            if occupied:
                print(f"recover.error=database_locked {reason}")
                return 1
            if not reason:
                print("recover.status=already_openable")
                return 0

            wal_size = wal.stat().st_size if wal.exists() else 0
            backups: list[Path] = []
            if not args.skip_backup:
                args.backup_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                for source in (database, wal):
                    if not source.exists():
                        continue
                    target = args.backup_dir / f"{source.name}.pre-recovery-{stamp}"
                    try:
                        shutil.copy2(source, target)
                    except OSError as exc:
                        print(f"recover.error=backup_failed {type(exc).__name__}: {exc}")
                        print(
                            "recover.hint=the database was not modified; "
                            "fix the backup path and retry"
                        )
                        return 1
                    backups.append(target)
                    print(f"recover.backup={target}")

            escaped = database.as_posix().replace("'", "''")
            con = duckdb.connect(":memory:")
            try:
                con.execute(f"ATTACH '{escaped}' AS target")
                # The checkpoint must name the attached database: a bare
                # CHECKPOINT only applies to the default database, which here is
                # the in-memory one.
                con.execute("CHECKPOINT target")
                before = _row_count(con, "raw", "vendor_snapshot")
            except Exception as exc:  # noqa: BLE001 - report any recovery failure verbatim
                print(f"recover.error={type(exc).__name__}: {exc}")
                return 1
            finally:
                con.close()

            if wal.exists():
                print(f"recover.error=wal_not_checkpointed size={wal.stat().st_size}")
                return 1

            verify = duckdb.connect(str(database))
            try:
                after = _row_count(verify, "raw", "vendor_snapshot")
            finally:
                verify.close()
            print(f"recover.vendor_snapshot_rows={after}")
            if before >= 0 and after != before:
                print("recover.error=row_count_mismatch")
                return 1

            _write_audit(database, wal_size, backups)
            print("recover.status=recovered")
            print(f"recover.database={database}")
            print(f"recover.wal_remaining={wal.exists()}")
            return 0
    except DuckDbWriteLockTimeout:
        print("recover.error=writer_lock_held")
        print("recover.hint=another writer holds the database lock; stop it and retry")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
