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


@dataclass(frozen=True)
class StorePaths:
    pdf: Path
    md: Path
    meta: Path


@dataclass(frozen=True)
class FetchOptions:
    library_dir: Path
    out_dir: Path | None
    project_files: str
    link_mode: str
    overwrite: bool
    force_download: bool
    refresh_md: bool
    dry_run: bool
    download_timeout: int
    marker_timeout: int
    download_retries: int
    marker_retries: int
    download_backoff: float
    marker_backoff: float
    workers: int
    min_md_chars: int
    min_md_lines: int
    allow_low_quality_md: bool
    verbose: bool
    extractor: str  # "auto", "marker", "arxiv_html"


@dataclass
class ProcessResult:
    success: bool
    paper: PaperInput
    key: str
    index_entry: dict[str, Any] | None = None
    error: str | None = None
    dry_run: bool = False
