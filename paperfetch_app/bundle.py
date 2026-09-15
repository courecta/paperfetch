from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import ensure_dir, read_json, sha256_file, write_json_atomic


@dataclass(frozen=True)
class BundlePaths:
    root: Path
    meta: Path
    source: Path
    pdf: Path
    document: Path
    markdown: Path
    text: Path
    refs_bib: Path
    references: Path
    figures_dir: Path
    tables_dir: Path
    crops_dir: Path
    pages_dir: Path
    checksums: Path
    report: Path

    def figures_manifest(self) -> Path:
        return self.root / "figures.json"

    def tables_manifest(self) -> Path:
        return self.root / "tables.json"


def bundle_paths(library_dir: Path, key: str) -> BundlePaths:
    return replace_root(library_dir / key)


def staging_root(library_dir: Path, key: str) -> Path:
    return library_dir / ".staging" / f"{key}-{uuid.uuid4().hex[:8]}"


@contextmanager
def staging_bundle(library_dir: Path, key: str) -> Iterator[BundlePaths]:
    root = staging_root(library_dir, key)
    ensure_dir(root)
    paths = replace_root(root)
    try:
        yield paths
    finally:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)


def replace_root(root: Path) -> BundlePaths:
    return BundlePaths(
        root=root,
        meta=root / "meta.json",
        source=root / "source.html",
        pdf=root / "paper.pdf",
        document=root / "document.json",
        markdown=root / "paper.md",
        text=root / "paper.txt",
        refs_bib=root / "refs.bib",
        references=root / "references.json",
        figures_dir=root / "figures",
        tables_dir=root / "tables",
        crops_dir=root / "crops",
        pages_dir=root / "pages",
        checksums=root / "checksums.json",
        report=root / "extraction_report.json",
    )


def promote(staging: Path, final: Path) -> None:
    """Atomically replace ``final`` with ``staging``.

    The previous bundle (if any) is moved aside first, so a crash mid-promote
    leaves either the old or the new bundle in place.
    """
    ensure_dir(final.parent)
    trash = final.parent / ".trash"
    ensure_dir(trash)
    backup = trash / f"{final.name}-{uuid.uuid4().hex[:8]}"
    had_old = final.exists()
    if had_old:
        os.replace(final, backup)
    try:
        os.replace(staging, final)
    except OSError:
        if had_old and backup.exists() and not final.exists():
            os.replace(backup, final)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def list_bundle_dirs(library_dir: Path) -> list[Path]:
    if not library_dir.exists():
        return []
    result = []
    for child in sorted(library_dir.iterdir()):
        if child.is_dir() and not child.name.startswith(".") and (child / "meta.json").exists():
            result.append(child)
    return result


def load_bundle_meta(root: Path) -> dict[str, Any]:
    payload = read_json(root / "meta.json", default=None)
    return payload if isinstance(payload, dict) else {}


def write_checksums(paths: BundlePaths) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for file_path in sorted(paths.root.rglob("*")):
        if not file_path.is_file() or file_path.name in {".checksums.tmp"}:
            continue
        if file_path == paths.checksums:
            continue
        checksums[str(file_path.relative_to(paths.root))] = sha256_file(file_path)
    write_json_atomic(paths.checksums, checksums)
    return checksums
