from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import get_venv_bin_dir
from .errors import MarkerUnavailableError
from .io_utils import ensure_dir, retry_call

MARKER_SEMAPHORE = threading.BoundedSemaphore(1)
_MARKER_COMMAND_CACHE: dict[str, str | None] = {}


@dataclass(frozen=True)
class MarkerResult:
    markdown_path: Path
    output_dir: Path


def _log(verbose: bool, message: str) -> None:
    if verbose:
        print(message, file=sys.stderr, flush=True)


def ensure_marker_command(marker_venv: Path, allow_install: bool, verbose: bool) -> str | None:
    cache_key = f"{marker_venv}:{allow_install}"
    if cache_key in _MARKER_COMMAND_CACHE:
        return _MARKER_COMMAND_CACHE[cache_key]

    for cmd in ("marker_single", "marker"):
        found = shutil.which(cmd)
        if found:
            _log(verbose, f"[marker] using system command: {found}")
            _MARKER_COMMAND_CACHE[cache_key] = found
            return found

    venv_bin = get_venv_bin_dir(marker_venv)
    venv_marker_single = venv_bin / "marker_single"
    venv_marker = venv_bin / "marker"
    if venv_marker_single.exists():
        _MARKER_COMMAND_CACHE[cache_key] = str(venv_marker_single)
        return str(venv_marker_single)
    if venv_marker.exists():
        _MARKER_COMMAND_CACHE[cache_key] = str(venv_marker)
        return str(venv_marker)

    if not allow_install:
        _MARKER_COMMAND_CACHE[cache_key] = None
        return None

    ensure_dir(marker_venv.parent)
    _log(verbose, f"[marker] creating isolated venv at {marker_venv}")
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(marker_venv)],
            check=True,
            capture_output=not verbose,
            text=True,
        )
        pip_bin = venv_bin / "pip"
        subprocess.run(
            [str(pip_bin), "install", "marker-pdf", "--quiet"],
            check=True,
            capture_output=not verbose,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        _log(verbose, f"[marker] setup failed: {exc}")
        _MARKER_COMMAND_CACHE[cache_key] = None
        return None

    if venv_marker_single.exists():
        _MARKER_COMMAND_CACHE[cache_key] = str(venv_marker_single)
        return str(venv_marker_single)
    if venv_marker.exists():
        _MARKER_COMMAND_CACHE[cache_key] = str(venv_marker)
        return str(venv_marker)
    _MARKER_COMMAND_CACHE[cache_key] = None
    return None


def _run_marker_once(
    marker_cmd: str,
    pdf_path: Path,
    marker_out_dir: Path,
    timeout_sec: int,
    verbose: bool,
) -> None:
    cmd = [
        marker_cmd,
        str(pdf_path),
        "--output_dir",
        str(marker_out_dir),
        "--output_format",
        "markdown",
    ]
    completed = subprocess.run(
        cmd,
        capture_output=not verbose,
        text=True,
        timeout=timeout_sec,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "")[-500:]
        raise RuntimeError(f"marker failed with code {completed.returncode}: {stderr}")


def find_markdown(output_dir: Path, pdf_path: Path) -> Path | None:
    expected = output_dir / pdf_path.stem / f"{pdf_path.stem}.md"
    if expected.exists():
        return expected
    candidates = sorted(output_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def extract_with_marker(
    marker_cmd: str,
    pdf_path: Path,
    work_dir: Path,
    timeout_sec: int,
    retries: int,
    backoff_seconds: float,
    verbose: bool,
) -> MarkerResult:
    """Run marker for a single PDF inside a dedicated work directory.

    The semaphore serializes marker invocations because marker is memory/GPU
    heavy; parallel runs are a common cause of OOM kills.
    """
    ensure_dir(work_dir)
    with MARKER_SEMAPHORE:
        retry_call(
            action_name="marker extraction",
            func=lambda: _run_marker_once(marker_cmd, pdf_path, work_dir, timeout_sec, verbose),
            retries=retries,
            backoff_seconds=backoff_seconds,
            retriable_exceptions=(RuntimeError, subprocess.TimeoutExpired, OSError),
        )

    markdown_path = find_markdown(work_dir, pdf_path)
    if markdown_path is None:
        raise MarkerUnavailableError("marker completed but no markdown output was found")
    return MarkerResult(markdown_path=markdown_path, output_dir=markdown_path.parent)


def require_marker_command(marker_venv: Path, allow_install: bool, verbose: bool) -> str:
    cmd = ensure_marker_command(marker_venv, allow_install, verbose)
    if cmd is None:
        raise MarkerUnavailableError(
            "marker is not installed and could not be installed automatically",
            hint="Install marker-pdf (pip install marker-pdf) or pass --no-install-marker after installing it.",
        )
    return cmd
