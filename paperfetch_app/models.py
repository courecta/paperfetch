from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PaperInput:
    slug: str
    title: str
    url: str
    source: str


@dataclass(frozen=True)
class PaperIdentity:
    kind: str
    value: str
    fingerprint: str
    key: str
    normalized_url: str
    version: str | None = None


@dataclass(frozen=True)
class StorePaths:
    pdf: Path
    md: Path
    meta: Path


@dataclass(frozen=True)
class FetchOptions:
    library_dir: Path
    out_dir: Path | None = None
    project_files: str = "md"
    link_mode: str = "copy"
    overwrite: bool = False
    force_download: bool = False
    refresh_md: bool = False
    dry_run: bool = False
    download_timeout: int = 120
    marker_timeout: int = 900
    download_retries: int = 3
    marker_retries: int = 1
    download_backoff: float = 1.5
    marker_backoff: float = 2.0
    workers: int = 4
    verbose: bool = False
    extractor: str = "auto"  # auto | arxiv_html | marker
    min_coverage: float = 0.95
    allow_incomplete: bool = False
    download_figures: bool = True
    pdf_visual: bool = True
    # Full-page renders are large and nothing reads them; opt in explicitly.
    page_images: bool = False
    max_asset_bytes: int = 25 * 1024 * 1024
    marker_venv: Path | None = None
    install_marker: bool = True
    pdf_path: Path | None = None


@dataclass
class ProcessResult:
    success: bool
    paper: PaperInput
    key: str
    index_entry: dict[str, Any] | None = None
    error: str | None = None
    dry_run: bool = False
    resolution: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
