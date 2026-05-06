from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .io_utils import ensure_dir


@contextmanager
def file_lock(lock_path: Path, timeout_sec: float = 60.0, poll_interval: float = 0.1) -> Iterator[None]:
    ensure_dir(lock_path.parent)
    start = time.monotonic()

    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if (time.monotonic() - start) >= timeout_sec:
                    raise TimeoutError(f"Timed out acquiring lock: {lock_path}")
                time.sleep(poll_interval)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
