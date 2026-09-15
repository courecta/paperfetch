from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from .io_utils import ensure_dir


@contextmanager
def file_lock(lock_path: Path, timeout_sec: float = 60.0, poll_interval: float = 0.1) -> Iterator[None]:
    """Cross-platform advisory file lock.

    ``poll_interval`` is accepted for backwards compatibility; filelock polls
    internally.
    """
    del poll_interval
    ensure_dir(lock_path.parent)
    lock = FileLock(str(lock_path), timeout=timeout_sec)
    try:
        with lock:
            yield
    except FileLockTimeout as exc:
        raise TimeoutError(f"Timed out acquiring lock: {lock_path}") from exc
