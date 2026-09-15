"""Render PDF pages and float crops as visual ground truth.

Crops come from the layout model's own bounding boxes. The previous approach
searched the page for the caption text and then guessed a region roughly half a
page tall around it, which mislocated floats whenever a label appeared twice or
a figure did not sit where the heuristic assumed. marker reports bboxes in PDF
points, so the exact region is already known.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from .bundle import BundlePaths
from .extract.ir import Document
from .io_utils import ensure_dir, safe_slug, write_json_atomic


def _crop_margins(bbox: list[float], width: float, height: float) -> tuple[float, float, float, float] | None:
    """Convert a top-left-origin bbox into pypdfium2 (left, bottom, right, top) margins."""
    x0, y0, x1, y1 = bbox
    left, right = max(0.0, min(x0, x1)), min(width, max(x0, x1))
    top, bottom = max(0.0, min(y0, y1)), min(height, max(y0, y1))
    if right - left < 1.0 or bottom - top < 1.0:
        return None
    return (left, height - bottom, width - right, top)


def _float_targets(document: Document) -> list[tuple[str, str, list[float], int]]:
    targets: list[tuple[str, str, list[float], int]] = []
    for block in document.blocks:
        if block.kind not in {"figure", "table"}:
            continue
        bbox = block.meta.get("bbox")
        page = block.meta.get("page")
        if not isinstance(bbox, list) or len(bbox) != 4 or not isinstance(page, int):
            continue
        targets.append((block.id, block.kind, [float(v) for v in bbox], page))
    return targets


def render_pdf_visuals(
    document: Document,
    pdf_path: Path,
    paths: BundlePaths,
    *,
    page_dpi: int = 120,
    crop_dpi: int = 180,
) -> dict[str, Any]:
    if not pdf_path.exists():
        return {"available": False, "reason": "PDF missing"}

    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:
        return {"available": False, "reason": f"cannot open PDF: {exc}"}

    ensure_dir(paths.pages_dir)
    ensure_dir(paths.crops_dir)

    report: dict[str, Any] = {
        "available": True,
        "page_count": len(pdf),
        "pages": [],
        "crops": [],
        "float_pages": {},
    }

    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            image = page.render(scale=page_dpi / 72.0).to_pil()
            page_path = paths.pages_dir / f"page-{page_index + 1:03d}.png"
            image.save(page_path)
            report["pages"].append(f"pages/{page_path.name}")

        for element_id, kind, bbox, page_index in _float_targets(document):
            if not 0 <= page_index < len(pdf):
                continue
            page = pdf[page_index]
            width, height = page.get_size()
            margins = _crop_margins(bbox, width, height)
            if margins is None:
                continue
            report["float_pages"][element_id] = page_index + 1
            image = page.render(scale=crop_dpi / 72.0, crop=margins).to_pil()
            crop_name = f"{safe_slug(element_id, max_length=50)}.png"
            image.save(paths.crops_dir / crop_name)
            report["crops"].append(
                {
                    "id": element_id,
                    "kind": kind,
                    "page": page_index + 1,
                    "path": f"crops/{crop_name}",
                }
            )
    finally:
        pdf.close()

    write_json_atomic(paths.crops_dir / "manifest.json", report)
    return report
