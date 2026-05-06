from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .config import get_venv_bin_dir
from .io_utils import ensure_dir, retry_call


def ensure_marker_command(marker_venv: Path, allow_install: bool, verbose: bool) -> str | None:
    for cmd in ("marker_single", "marker"):
        found = shutil.which(cmd)
        if found:
            if verbose:
                print(f"[marker] using system command: {found}")
            return found

    venv_bin = get_venv_bin_dir(marker_venv)
    venv_marker_single = venv_bin / "marker_single"
    venv_marker = venv_bin / "marker"
    if venv_marker_single.exists():
        return str(venv_marker_single)
    if venv_marker.exists():
        return str(venv_marker)

    if not allow_install:
        return None

    ensure_dir(marker_venv.parent)
    print(f"[marker] creating isolated venv at {marker_venv}")
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
    except subprocess.CalledProcessError:
        return None

    if venv_marker_single.exists():
        return str(venv_marker_single)
    if venv_marker.exists():
        return str(venv_marker)
    return None


def _run_marker_once(
    marker_cmd: str,
    pdf_path: Path,
    marker_out_dir: Path,
    timeout_sec: int,
    verbose: bool,
) -> None:
    cmd = [marker_cmd, str(pdf_path), "--output_dir", str(marker_out_dir)]
    completed = subprocess.run(
        cmd,
        capture_output=not verbose,
        text=True,
        timeout=timeout_sec,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "")[-300:]
        raise RuntimeError(f"marker failed with code {completed.returncode}: {stderr}")


def extract_with_marker(
    marker_cmd: str,
    pdf_path: Path,
    slug: str,
    marker_out_dir: Path,
    timeout_sec: int,
    retries: int,
    backoff_seconds: float,
    verbose: bool,
) -> Path:
    ensure_dir(marker_out_dir)
    before = set(marker_out_dir.rglob("*.md"))

    retry_call(
        action_name="marker extraction",
        func=lambda: _run_marker_once(marker_cmd, pdf_path, marker_out_dir, timeout_sec, verbose),
        retries=retries,
        backoff_seconds=backoff_seconds,
        retriable_exceptions=(RuntimeError, subprocess.TimeoutExpired, OSError),
    )

    preferred = marker_out_dir / slug / f"{slug}.md"
    if preferred.exists():
        return preferred

    after = set(marker_out_dir.rglob("*.md"))
    new_files = sorted(after - before, key=lambda p: p.stat().st_mtime, reverse=True)
    if new_files:
        return new_files[0]

    all_files = sorted(after, key=lambda p: p.stat().st_mtime, reverse=True)
    if all_files:
        return all_files[0]

    raise RuntimeError("marker completed but no markdown output was found")
