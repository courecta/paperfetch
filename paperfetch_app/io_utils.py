from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar


T = TypeVar("T")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned.strip("-") or "paper"


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
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


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
            sleep_for = backoff_seconds * (2**attempt)
            time.sleep(sleep_for)
            attempt += 1


def check_markdown_quality(md_path: Path, min_chars: int, min_lines: int) -> tuple[bool, str]:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    non_ws_chars = len("".join(text.split()))
    line_count = len([line for line in text.splitlines() if line.strip()])

    if non_ws_chars < min_chars:
        return False, f"low markdown content: chars={non_ws_chars} < min={min_chars}"
    if line_count < min_lines:
        return False, f"low markdown content: lines={line_count} < min={min_lines}"
    return True, "ok"
