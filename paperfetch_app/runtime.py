from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import get_default_marker_venv
from .locking import file_lock
from .models import FetchOptions, PaperInput, ProcessResult
from .pipeline import run_fetch
from .storage import index_path, load_index, save_index


def default_fetch_options(
    library_dir: Path,
    *,
    extractor: str = "auto",
    force_download: bool = False,
    refresh_md: bool = False,
    verbose: bool = False,
    marker_venv: Path | None = None,
    install_marker: bool = True,
    prefer_pymupdf: bool = False,
    out_dir: Path | None = None,
    project_files: str = "none",
    workers: int = 4,
    **overrides: Any,
) -> FetchOptions:
    values: dict[str, Any] = dict(
        library_dir=library_dir,
        out_dir=out_dir,
        project_files=project_files,
        link_mode="copy",
        overwrite=False,
        force_download=force_download,
        refresh_md=refresh_md,
        dry_run=False,
        download_timeout=120,
        marker_timeout=900,
        download_retries=3,
        marker_retries=1,
        download_backoff=1.5,
        marker_backoff=2.0,
        workers=max(1, int(workers)),
        min_md_chars=0,
        min_md_lines=0,
        allow_low_quality_md=True,
        verbose=verbose,
        extractor=extractor,
        min_coverage=0.95,
        allow_incomplete=False,
        download_figures=True,
        pdf_visual=True,
        max_asset_bytes=25 * 1024 * 1024,
        marker_venv=(marker_venv or get_default_marker_venv()).expanduser().resolve(),
        install_marker=install_marker,
        prefer_pymupdf=prefer_pymupdf,
    )
    values.update(overrides)
    return FetchOptions(**values)


def locked_load_index(library_dir: Path, *, timeout: float = 120.0) -> dict[str, dict[str, Any]]:
    with file_lock(library_dir / "index.lock", timeout_sec=timeout):
        return load_index(index_path(library_dir))


def locked_merge_index(library_dir: Path, updates: dict[str, dict[str, Any]], *, timeout: float = 120.0) -> None:
    if not updates:
        return
    with file_lock(library_dir / "index.lock", timeout_sec=timeout):
        current = load_index(index_path(library_dir))
        current.update(updates)
        save_index(index_path(library_dir), current)

    try:
        from . import db as db_module

        conn = db_module.connect(library_dir / "library.sqlite3")
        db_module.init_db(conn)
        try:
            for key in updates:
                db_module.index_bundle(conn, library_dir, key)
        finally:
            conn.close()
    except Exception:
        pass


def fetch_and_record(
    papers: list[PaperInput],
    options: FetchOptions,
    marker_cmd: str | None = None,
) -> list[ProcessResult]:
    """Fetch papers and atomically merge successful entries into the index."""
    snapshot = locked_load_index(options.library_dir)
    results = run_fetch(papers, options, marker_cmd, snapshot)
    updates = {
        result.key: result.index_entry
        for result in results
        if result.success and not result.dry_run and result.index_entry
    }
    locked_merge_index(options.library_dir, updates)
    return results
