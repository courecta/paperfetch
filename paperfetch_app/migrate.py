from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .bundle import bundle_paths, promote, staging_bundle, write_checksums
from .extract.render_text import strip_markdown
from .io_utils import now_utc_iso, write_json_atomic
from .storage import index_path, load_index, save_index


def migrate_library(library_dir: Path, *, delete_old: bool = False) -> dict[str, Any]:
    """Convert legacy flat ``md/``/``pdfs/``/``meta/`` entries into bundles."""
    library_dir = library_dir.expanduser().resolve()
    idx = load_index(index_path(library_dir))
    migrated: list[str] = []
    skipped: list[str] = []

    for key, entry in list(idx.items()):
        final = bundle_paths(library_dir, key)
        if final.meta.exists():
            entry["md"] = f"{key}/paper.md"
            if final.pdf.exists() or entry.get("pdf"):
                entry["pdf"] = f"{key}/paper.pdf"
            entry["document"] = f"{key}/document.json"
            skipped.append(key)
            continue

        md_rel = entry.get("md")
        pdf_rel = entry.get("pdf")
        md_path = library_dir / md_rel if isinstance(md_rel, str) else None
        pdf_path = library_dir / pdf_rel if isinstance(pdf_rel, str) else None
        if md_path is None or not md_path.exists():
            skipped.append(key)
            continue

        with staging_bundle(library_dir, key) as staging:
            markdown_text = md_path.read_text(encoding="utf-8", errors="replace")
            staging.markdown.write_text(markdown_text, encoding="utf-8")
            staging.text.write_text(strip_markdown(markdown_text), encoding="utf-8")
            if pdf_path is not None and pdf_path.exists():
                shutil.copy2(pdf_path, staging.pdf)

            meta_payload = dict(entry)
            meta_payload.update(
                {
                    "key": key,
                    "md": "paper.md",
                    "pdf": "paper.pdf" if staging.pdf.exists() else None,
                    "document": None,
                    "updated_at": now_utc_iso(),
                    "migrated_from_legacy": True,
                    "coverage": entry.get("coverage") or {"available": False, "migrated": True},
                }
            )
            write_json_atomic(staging.meta, meta_payload)
            write_json_atomic(staging.report, {"key": key, "migrated": True})
            write_checksums(staging)
            promote(staging.root, final.root)

        entry["md"] = f"{key}/paper.md"
        entry["pdf"] = f"{key}/paper.pdf" if final.pdf.exists() else None
        entry["document"] = f"{key}/document.json"
        entry["migrated_from_legacy"] = True
        migrated.append(key)

    save_index(index_path(library_dir), idx)

    if delete_old and migrated:
        for sub in ("md", "pdfs", "meta", "converted"):
            target = library_dir / sub
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

    return {
        "migrated": len(migrated),
        "skipped": len(skipped),
        "keys": migrated,
        "old_tree_removed": bool(delete_old and migrated),
    }
