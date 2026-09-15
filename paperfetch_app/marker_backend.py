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

# marker's dependency tree (torch, pillow, surya) ships wheels well behind the
# newest CPython. Building the venv with sys.executable ties marker's supported
# Python range to paperfetch's own, so on a newer interpreter the install fails
# while compiling from source and silently degrades to PyMuPDF.
MARKER_PYTHON_MIN = (3, 10)
MARKER_PYTHON_MAX = (3, 13)


def _interpreter_version(executable: str) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            [executable, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        major, minor = (int(part) for part in out.stdout.split())
    except ValueError:
        return None
    return (major, minor)


def _select_interpreter(verbose: bool) -> str | None:
    """Find an interpreter marker can actually install under."""
    candidates = [sys.executable]
    for minor in range(MARKER_PYTHON_MAX[1], MARKER_PYTHON_MIN[1] - 1, -1):
        found = shutil.which(f"python3.{minor}")
        if found:
            candidates.append(found)

    for candidate in candidates:
        version = _interpreter_version(candidate)
        if version is None:
            continue
        if MARKER_PYTHON_MIN <= version <= MARKER_PYTHON_MAX:
            _log(verbose, f"[marker] using interpreter {candidate} (python{version[0]}.{version[1]})")
            return candidate
        _log(verbose, f"[marker] skipping {candidate}: python{version[0]}.{version[1]} is out of range")
    return None


@dataclass(frozen=True)
class MarkerResult:
    markdown_path: Path | None
    output_dir: Path
    json_path: Path | None = None


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

    interpreter = _select_interpreter(verbose)
    if interpreter is None:
        raise MarkerUnavailableError(
            f"no interpreter in range python{MARKER_PYTHON_MIN[0]}.{MARKER_PYTHON_MIN[1]}"
            f"-{MARKER_PYTHON_MAX[0]}.{MARKER_PYTHON_MAX[1]} is available to install marker",
            hint=(
                f"Install one (for example python3.{MARKER_PYTHON_MAX[1]}), or pass --prefer-pymupdf "
                "to accept degraded extraction with no structured IR."
            ),
        )

    ensure_dir(marker_venv.parent)
    _log(verbose, f"[marker] creating isolated venv at {marker_venv}")
    try:
        subprocess.run(
            [interpreter, "-m", "venv", str(marker_venv)],
            check=True,
            capture_output=not verbose,
            text=True,
        )
        pip_bin = venv_bin / "pip"
        subprocess.run(
            # --only-binary fails fast on a missing wheel instead of attempting
            # a source build that dies on absent system headers.
            [str(pip_bin), "install", "--only-binary=:all:", "marker-pdf", "--quiet"],
            check=True,
            capture_output=not verbose,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "")[-800:] if hasattr(exc, "stderr") else ""
        _log(verbose, f"[marker] setup failed: {exc}\n{stderr}")
        raise MarkerUnavailableError(
            f"marker install failed under {interpreter}",
            hint=stderr.strip() or "Re-run with --verbose to see the pip output.",
        ) from exc

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
    output_format: str = "json",
) -> None:
    cmd = [
        marker_cmd,
        str(pdf_path),
        "--output_dir",
        str(marker_out_dir),
        "--output_format",
        output_format,
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


def _find_output(output_dir: Path, pdf_path: Path, suffix: str) -> Path | None:
    expected = output_dir / pdf_path.stem / f"{pdf_path.stem}{suffix}"
    if expected.exists():
        return expected
    candidates = sorted(output_dir.rglob(f"*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def find_markdown(output_dir: Path, pdf_path: Path) -> Path | None:
    return _find_output(output_dir, pdf_path, ".md")


def find_json(output_dir: Path, pdf_path: Path) -> Path | None:
    return _find_output(output_dir, pdf_path, ".json")


def extract_with_marker(
    marker_cmd: str,
    pdf_path: Path,
    work_dir: Path,
    timeout_sec: int,
    retries: int,
    backoff_seconds: float,
    verbose: bool,
    output_format: str = "json",
) -> MarkerResult:
    """Run marker for a single PDF inside a dedicated work directory.

    The semaphore serializes marker invocations because marker is memory/GPU
    heavy; parallel runs are a common cause of OOM kills.
    """
    ensure_dir(work_dir)
    with MARKER_SEMAPHORE:
        retry_call(
            action_name="marker extraction",
            func=lambda: _run_marker_once(marker_cmd, pdf_path, work_dir, timeout_sec, verbose, output_format),
            retries=retries,
            backoff_seconds=backoff_seconds,
            retriable_exceptions=(RuntimeError, subprocess.TimeoutExpired, OSError),
        )

    if output_format == "json":
        json_path = find_json(work_dir, pdf_path)
        if json_path is None:
            raise MarkerUnavailableError("marker completed but no JSON output was found")
        return MarkerResult(markdown_path=None, output_dir=json_path.parent, json_path=json_path)

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
