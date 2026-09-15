from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .bundle import BundlePaths
from .extract.ir import Document
from .io_utils import ensure_dir, safe_slug, write_json_atomic


def _import_fitz() -> Any | None:
    try:
        import pymupdf as fitz  # type: ignore

        return fitz
    except Exception:
        pass
    try:
        import fitz  # type: ignore

        return fitz
    except Exception:
        return None


def render_pdf_visuals(
    document: Document,
    pdf_path: Path,
    paths: BundlePaths,
    *,
    page_dpi: int = 120,
    crop_dpi: int = 180,
) -> dict[str, Any]:
    """Render full PDF pages plus approximate float crops as visual ground truth."""
    fitz = _import_fitz()
    if fitz is None:
        return {"available": False, "reason": "PyMuPDF not installed"}
    if not pdf_path.exists():
        return {"available": False, "reason": "PDF missing"}

    ensure_dir(paths.pages_dir)
    ensure_dir(paths.crops_dir)

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:  # pragma: no cover - corrupt pdf handling
        return {"available": False, "reason": f"cannot open PDF: {exc}"}

    report: dict[str, Any] = {
        "available": True,
        "page_count": doc.page_count,
        "pages": [],
        "crops": [],
        "float_pages": {},
    }
    try:
        zoom = page_dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            page_path = paths.pages_dir / f"page-{page_index + 1:03d}.png"
            pix.save(str(page_path))
            report["pages"].append(f"pages/{page_path.name}")

        targets: list[tuple[str, str, str]] = []
        for block in document.blocks:
            if block.kind == "figure" and block.figure is not None and block.figure.label:
                targets.append((block.id or block.figure.id, block.figure.label, "figure"))
            elif block.kind == "table" and block.table is not None and block.table.label:
                targets.append((block.id or block.table.id, block.table.label, "table"))

        crop_matrix = fitz.Matrix(crop_dpi / 72.0, crop_dpi / 72.0)
        for element_id, label, kind in targets:
            needle = re.sub(r"\s+", " ", str(label)).strip().rstrip(":")
            found = _find_label(doc, needle)
            if found is None:
                continue
            page_index, rect = found
            report["float_pages"][element_id] = page_index + 1
            page = doc.load_page(page_index)
            crop_rect = _crop_rect(page.rect, rect, kind)
            pix = page.get_pixmap(matrix=crop_matrix, clip=crop_rect, alpha=False)
            crop_name = f"{safe_slug(element_id, max_length=50)}.png"
            crop_path = paths.crops_dir / crop_name
            pix.save(str(crop_path))
            report["crops"].append({"id": element_id, "label": needle, "kind": kind, "page": page_index + 1, "path": f"crops/{crop_name}"})
    finally:
        doc.close()

    write_json_atomic(paths.crops_dir / "manifest.json", report)
    return report


def _find_label(doc: Any, needle: str) -> tuple[int, Any] | None:
    if not needle:
        return None
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        hits = page.search_for(needle)
        if hits:
            return page_index, hits[0]
    return None


def _crop_rect(page_rect: Any, caption_rect: Any, kind: str) -> Any:
    height = page_rect.height
    margin = 0.04 * height
    span = 0.55 * height
    if kind == "table":
        top = max(page_rect.y0, caption_rect.y0 - margin)
        bottom = min(page_rect.y1, caption_rect.y1 + span)
    else:
        top = max(page_rect.y0, caption_rect.y0 - span)
        bottom = min(page_rect.y1, caption_rect.y1 + margin)
    return _rect(page_rect.x0, top, page_rect.x1, bottom)


def _rect(x0: float, y0: float, x1: float, y1: float) -> Any:
    fitz = _import_fitz()
    return fitz.Rect(x0, y0, x1, y1)
