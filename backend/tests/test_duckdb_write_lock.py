import os
import time
from pathlib import Path

import pytest

from app.utils.duckdb_write_lock import (
    DuckDbWriteLockTimeout,
    duckdb_write_lock,
)


def test_lock_acquires_and_releases(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    with duckdb_write_lock(lock):
        assert lock.exists()
        assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert not lock.exists()


def test_second_process_times_out_with_holder_info(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    with duckdb_write_lock(lock):
        with pytest.raises(DuckDbWriteLockTimeout, match="pid"):
            with duckdb_write_lock(
                lock, timeout_seconds=0.5, poll_interval_seconds=0.1
            ):
                pass
    # Released after the holder exits: acquisition succeeds again.
    with duckdb_write_lock(lock, timeout_seconds=1, poll_interval_seconds=0.1):
        pass


def test_stale_lock_is_seized(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    lock.write_text("999999\n", encoding="utf-8")
    stale = time.time() - 3600
    os.utime(lock, (stale, stale))

    with duckdb_write_lock(lock, timeout_seconds=1, stale_seconds=1800):
        assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert not lock.exists()


def test_exception_inside_lock_still_releases(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    with pytest.raises(RuntimeError, match="boom"):
        with duckdb_write_lock(lock):
            raise RuntimeError("boom")
    assert not lock.exists()
