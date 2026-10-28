"""OS writer locks with diagnostic PID files; live holders never expire."""
from __future__ import annotations

import errno
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class DuckDbWriteLockTimeout(RuntimeError):
    """The writer lock was not available before the timeout."""


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _legacy_holder_alive(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    # A modern holder has released the OS guard, possibly through process death.
    if len(lines) > 1 and lines[1] == "os_lock_v1":
        return False
    try:
        return _process_alive(int(lines[0]))
    except (ValueError, IndexError):
        return True


@contextmanager
def duckdb_write_lock(
    lock_path: str | Path = "data/duckdb_write.lock",
    *,
    timeout_seconds: float = 60.0,
    stale_seconds: float = 1800.0,
    poll_interval_seconds: float = 1.0,
) -> Iterator[Path]:
    """Hold a persistent .guard OS lock and publish a temporary PID file.

    Never delete .guard: changing its inode would break mutual exclusion.
    stale_seconds remains accepted for compatibility; age never revokes ownership.
    Legacy PID-only files block while their process is alive or unknown.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    fd = os.open(str(path) + ".guard", os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        while True:
            locked = _try_lock(fd)
            if locked and not _legacy_holder_alive(path):
                break
            if locked:
                _unlock(fd)
                locked = False
            if time.monotonic() >= deadline:
                raise DuckDbWriteLockTimeout(
                    f"another process holds {path} (pid file); waited {timeout_seconds:.0f}s"
                )
            time.sleep(min(poll_interval_seconds, max(0, deadline - time.monotonic())))
        path.write_text(f"{os.getpid()}\nos_lock_v1\n", encoding="utf-8")
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)
    finally:
        if locked:
            _unlock(fd)
        os.close(fd)
