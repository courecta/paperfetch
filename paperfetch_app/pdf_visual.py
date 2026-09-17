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


# How far a float is assumed to extend from its caption, as a fraction of page
# height, when no bounding box is available.
CAPTION_SPAN = 0.55
CAPTION_MARGIN = 0.04


def _float_targets(document: Document) -> list[tuple[str, str, list[float], int]]:
    """Floats with a layout-model bbox, which can be cropped exactly."""
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


def _caption_targets(document: Document) -> list[tuple[str, str, str]]:
    """Floats with no bbox, identified by the caption text to search for.

    arXiv HTML carries no page geometry, so these are located by finding the
    caption on the page and taking a band around it. Approximate, but a roughly
    framed crop is more useful than none.
    """
    targets: list[tuple[str, str, str]] = []
    for block in document.blocks:
        if block.kind not in {"figure", "table"}:
            continue
        if block.meta.get("bbox"):
            continue
        float_obj = block.figure if block.kind == "figure" else block.table
        if float_obj is None:
            continue
        needle = (float_obj.label or "").strip().rstrip(":")
        if not needle:
            caption = "".join(i.text for i in (float_obj.caption or []) if i.text).strip()
            needle = caption[:40].rsplit(" ", 1)[0] if len(caption) > 40 else caption
        if needle:
            targets.append((block.id, block.kind, needle))
    return targets


def _find_caption(pdf: Any, needle: str) -> tuple[int, tuple[float, float, float, float]] | None:
    """First page containing this text, with its rect in PDF points."""
    for page_index in range(len(pdf)):
        textpage = pdf[page_index].get_textpage()
        try:
            searcher = textpage.search(needle, match_case=False, match_whole_word=False)
            hit = searcher.get_next()
            if hit is None:
                continue
            index, count = hit
            if textpage.count_rects(index, count) < 1:
                continue
            return page_index, tuple(textpage.get_rect(0))
        except Exception:
            continue
        finally:
            textpage.close()
    return None


def _band_around_caption(
    rect: tuple[float, float, float, float],
    kind: str,
    height: float,
) -> tuple[float, float, float, float]:
    """Crop margins for a band around a caption (pypdfium2 uses bottom-left origin)."""
    _, bottom, _, top = rect
    span, margin = CAPTION_SPAN * height, CAPTION_MARGIN * height
    if kind == "table":
        # Tables sit below their caption, so extend downwards.
        low, high = max(0.0, bottom - span), min(height, top + margin)
    else:
        # Figures sit above their caption.
        low, high = max(0.0, bottom - margin), min(height, top + span)
    return (0.0, low, 0.0, height - high)


def render_pdf_visuals(
    document: Document,
    pdf_path: Path,
    paths: BundlePaths,
    *,
    page_dpi: int = 120,
    crop_dpi: int = 180,
    page_images: bool = False,
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
        # Full-page renders are the largest thing in a bundle (~25% of a
        # library) and no tool, route or index reads them, so they are opt-in.
        if page_images:
            for page_index in range(len(pdf)):
                image = pdf[page_index].render(scale=page_dpi / 72.0).to_pil()
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
                    "precision": "exact",
                }
            )

        for element_id, kind, needle in _caption_targets(document):
            found = _find_caption(pdf, needle)
            if found is None:
                continue
            page_index, rect = found
            page = pdf[page_index]
            _, height = page.get_size()
            report["float_pages"][element_id] = page_index + 1
            image = page.render(scale=crop_dpi / 72.0, crop=_band_around_caption(rect, kind, height)).to_pil()
            crop_name = f"{safe_slug(element_id, max_length=50)}.png"
            image.save(paths.crops_dir / crop_name)
            report["crops"].append(
                {
                    "id": element_id,
                    "kind": kind,
                    "page": page_index + 1,
                    "path": f"crops/{crop_name}",
                    # Located from caption text, not a bounding box: the framing
                    # is a guess and may clip or include neighbouring content.
                    "precision": "approximate",
                }
            )
    finally:
        pdf.close()

    write_json_atomic(paths.crops_dir / "manifest.json", report)
    return report
