"""Small cross-platform exclusive lock for Attempt-local state files."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:  # pragma: no cover - selected by the host platform.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows.
    _fcntl = None

try:  # pragma: no cover - selected by the host platform.
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX.
    _msvcrt = None


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


class InterprocessFileLockUnavailable(RuntimeError):
    """Raised before entering a critical section when no OS lock is available."""


@contextmanager
def exclusive_file_lock(path: str | Path) -> Iterator[None]:
    """Hold one exclusive OS-backed lock, including between local processes."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock_for(lock_path)
    with thread_lock:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        backend = ""
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_EX)
                backend = "fcntl"
            elif _msvcrt is not None:
                _ensure_lock_byte(fd)
                while True:
                    try:
                        os.lseek(fd, 0, os.SEEK_SET)
                        _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
                        backend = "msvcrt"
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                raise InterprocessFileLockUnavailable(
                    "no supported interprocess file-lock backend is available"
                )
            yield
        finally:
            if backend == "fcntl":
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            elif backend == "msvcrt":
                os.lseek(fd, 0, os.SEEK_SET)
                _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
            os.close(fd)


def _ensure_lock_byte(fd: int) -> None:
    if os.fstat(fd).st_size > 0:
        return
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, b"\0")
    os.fsync(fd)


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock
