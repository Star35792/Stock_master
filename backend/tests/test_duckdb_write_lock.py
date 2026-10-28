import os
import subprocess
import sys
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
        assert lock.read_text(encoding="utf-8").splitlines()[0] == str(os.getpid())
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
        assert lock.read_text(encoding="utf-8").splitlines()[0] == str(os.getpid())
    assert not lock.exists()


def test_exception_inside_lock_still_releases(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    with pytest.raises(RuntimeError, match="boom"):
        with duckdb_write_lock(lock):
            raise RuntimeError("boom")
    assert not lock.exists()


def test_old_live_holder_cannot_be_seized(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    with duckdb_write_lock(lock):
        old = time.time() - 7200
        os.utime(lock, (old, old))
        with pytest.raises(DuckDbWriteLockTimeout):
            with duckdb_write_lock(lock, timeout_seconds=0, stale_seconds=1):
                pytest.fail("live holder was seized")
        assert lock.exists()
    assert not lock.exists()
    assert Path(str(lock) + ".guard").exists()


def test_live_legacy_pid_is_respected_even_when_old(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock, (old, old))
    with pytest.raises(DuckDbWriteLockTimeout):
        with duckdb_write_lock(lock, timeout_seconds=0):
            pytest.fail("live legacy writer was seized")
    assert lock.exists()


def test_process_crash_releases_os_lock(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    code = (
        "import os,sys; from app.utils.duckdb_write_lock import duckdb_write_lock; "
        "holder=duckdb_write_lock(sys.argv[1]); holder.__enter__(); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", code, str(lock)], check=True, timeout=15)
    assert lock.exists()  # Abrupt exit leaves diagnostics, but no OS ownership.
    with duckdb_write_lock(lock, timeout_seconds=0):
        pass
    assert not lock.exists()


def test_other_process_cannot_take_live_lock(tmp_path: Path) -> None:
    lock = tmp_path / "write.lock"
    code = (
        "import sys; from app.utils.duckdb_write_lock import duckdb_write_lock; "
        "holder=duckdb_write_lock(sys.argv[1],timeout_seconds=0); holder.__enter__()"
    )
    with duckdb_write_lock(lock):
        result = subprocess.run(
            [sys.executable, "-c", code, str(lock)], capture_output=True, timeout=15,
        )
        assert result.returncode != 0
        assert b"DuckDbWriteLockTimeout" in result.stderr
        assert lock.exists()
