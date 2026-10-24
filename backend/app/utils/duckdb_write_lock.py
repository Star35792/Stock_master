"""Cross-process write lock for the shared DuckDB database.

DuckDB has no server mode: a database file admits a single read-write
process at a time. Two of our processes writing concurrently (e.g. a
maintenance backfill script racing another script) fail late with
``Unique file handle conflict`` / IO lock errors, wasting minutes of work.

This lock does not remove the engine limitation — it coordinates *our*
processes so writers queue politely instead of failing mid-run:

* Atomic acquisition via ``O_CREAT | O_EXCL`` on a lock file next to the
  database (works on Windows and POSIX, no dependencies).
* The lock file records holder PID and timestamp; a leftover lock whose
  mtime is older than ``stale_seconds`` (crashed holder) can be seized.
* ``timeout_seconds`` bounds how long a caller waits before raising with
  a readable message identifying the current holder.

Scope note: FastAPI workers and schedulers do not take this lock (they are
the long-lived single writer for their schemas); it is for maintenance and
research scripts that run beside them. Longer term, splitting write domains
into separate database files (research vs market) removes the contention
entirely — see the storage ADRs in ``aiDoc/relations/``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class DuckDbWriteLockTimeout(RuntimeError):
    """Raised when the write lock cannot be acquired within the timeout."""


@contextmanager
def duckdb_write_lock(
    lock_path: str | Path = "data/duckdb_write.lock",
    *,
    timeout_seconds: float = 60.0,
    stale_seconds: float = 1800.0,
    poll_interval_seconds: float = 1.0,
) -> Iterator[Path]:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    last_holder = "unknown"
    while not acquired:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.close(fd)
            acquired = True
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                last_holder = path.read_text(encoding="utf-8").strip() or last_holder
            except OSError:
                age = 0.0
            if age > stale_seconds:
                # Holder crashed without releasing; seize the stale lock.
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise DuckDbWriteLockTimeout(
                    f"another process (pid {last_holder}) holds {path}; "
                    f"waited {timeout_seconds:.0f}s. Stop it, wait, or delete "
                    "the stale lock file if that process is gone."
                ) from None
            time.sleep(poll_interval_seconds)
    try:
        yield path
    finally:
        try:
            path.unlink()
        except OSError:
            pass
