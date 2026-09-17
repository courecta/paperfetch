from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_slug(value: str, max_length: int = 80) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned)
    cleaned = cleaned.strip("-") or "paper"
    if max_length and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip("-.")
    return cleaned or "paper"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    """Replace a JSON file atomically and durably.

    The temp name is unique per write: a name derived only from the target
    meant two processes writing the same file interleaved their bytes into one
    temp file before either renamed, publishing a spliced document. Both the
    file and its directory are fsynced, because a rename that reaches the
    directory before the data does leaves a zero-length file after a crash --
    which is how an index becomes unreadable.
    """
    ensure_dir(path.parent)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _fsync_dir(directory: Path) -> None:
    """Persist a rename. Best effort: not every filesystem allows this."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def retry_call(
    action_name: str,
    func: Callable[[], T],
    retries: int,
    backoff_seconds: float,
    retriable_exceptions: tuple[type[BaseException], ...],
) -> T:
    attempt = 0
    while True:
        try:
            return func()
        except retriable_exceptions as exc:
            if attempt >= retries:
                raise RuntimeError(f"{action_name} failed after {attempt + 1} attempts: {exc}") from exc
            window = backoff_seconds * (2**attempt)
            time.sleep(random.uniform(window / 2.0, window) if window > 0 else 0.0)
            attempt += 1


