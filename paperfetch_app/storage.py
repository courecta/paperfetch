from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .io_utils import ensure_dir, write_json_atomic
from .models import StorePaths


INDEX_FILENAME = "index.json"


def index_path(library_dir: Path) -> Path:
    return library_dir / INDEX_FILENAME


def ensure_library_paths(library_dir: Path, canonical_base: str) -> StorePaths:
    return StorePaths(
        pdf=library_dir / "pdfs" / f"{canonical_base}.pdf",
        md=library_dir / "md" / f"{canonical_base}.md",
        meta=library_dir / "meta" / f"{canonical_base}.json",
    )


def load_index(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        if isinstance(key, str) and isinstance(val, dict):
            out[key] = val
    return out


def save_index(path: Path, index: dict[str, dict[str, Any]]) -> None:
    write_json_atomic(path, index)


def materialize(src: Path, dst: Path, mode: str, overwrite: bool) -> None:
    ensure_dir(dst.parent)
    if dst.exists() or dst.is_symlink():
        if not overwrite:
            return
        dst.unlink()

    if mode == "copy":
        shutil.copy2(src, dst)
        return

    if mode == "symlink":
        os.symlink(src, dst)
        return

    if mode == "hardlink":
        try:
            os.link(src, dst)
            return
        except OSError:
            shutil.copy2(src, dst)
            return

    raise ValueError(f"Unsupported link mode: {mode}")


def list_entries(index: dict[str, dict[str, Any]], limit: int | None = None) -> list[tuple[str, dict[str, Any]]]:
    rows = sorted(index.items(), key=lambda kv: str(kv[1].get("updated_at", "")), reverse=True)
    if limit is None or limit <= 0:
        return rows
    return rows[:limit]


def clean_library(
    library_dir: Path,
    index: dict[str, dict[str, Any]],
    prune_missing_entries: bool,
    remove_orphans: bool,
) -> dict[str, int]:
    removed_index_entries = 0
    removed_orphans = 0

    active_rel: set[str] = set()
    keys_to_remove: list[str] = []

    for key, entry in index.items():
        pdf_rel = entry.get("pdf")
        md_rel = entry.get("md")
        if isinstance(pdf_rel, str):
            active_rel.add(pdf_rel)
        if isinstance(md_rel, str):
            active_rel.add(md_rel)

        pdf_exists = isinstance(pdf_rel, str) and (library_dir / pdf_rel).exists()
        md_exists = isinstance(md_rel, str) and (library_dir / md_rel).exists()
        if prune_missing_entries and not (pdf_exists and md_exists):
            keys_to_remove.append(key)

    for key in keys_to_remove:
        index.pop(key, None)
        removed_index_entries += 1

    if remove_orphans:
        scan_dirs = [library_dir / "pdfs", library_dir / "md", library_dir / "meta"]
        for root in scan_dirs:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(library_dir))
                if rel.startswith("meta/"):
                    continue
                if rel not in active_rel:
                    path.unlink(missing_ok=True)
                    removed_orphans += 1

    return {
        "removed_index_entries": removed_index_entries,
        "removed_orphans": removed_orphans,
    }
