from __future__ import annotations

from typing import Any

from .ir import Document

DEFAULT_MIN_RATIO = 0.95
TRACKED_KINDS = ("math", "figures", "tables", "bibitems", "notes")

# A stub page (for example arXiv's "no HTML available" placeholder) yields a
# near-empty body *and* nothing to account for. Either signal alone is
# legitimate -- a short note has few chars, a prose-only paper has no floats --
# so emptiness requires both.
MIN_CONTENT_CHARS = 1000


def _is_empty(document: Document, total_source: int) -> bool:
    if not document.blocks:
        return True
    return document.content_chars() < MIN_CONTENT_CHARS and total_source == 0


def audit(document: Document, *, min_ratio: float = DEFAULT_MIN_RATIO) -> dict[str, Any]:
    coverage = dict(document.coverage or {})
    source = coverage.get("source_counts", {}) or {}
    mapped = coverage.get("mapped_counts", {}) or {}
    unmapped = list(coverage.get("unmapped", []) or [])
    ir_available = bool(coverage.get("ir_available", True))

    figure_assets = coverage.get("assets", {}) or {}
    images_total = figure_assets.get("images_total")
    images_downloaded = figure_assets.get("images_downloaded")
    if images_total:
        missing = int(images_total) - int(images_downloaded or 0)
        if missing > 0:
            unmapped.append({"kind": "figure_images", "missing": missing})
        source = dict(source)
        mapped = dict(mapped)
        source["figure_images"] = int(images_total)
        mapped["figure_images"] = int(images_downloaded or 0)

    total_source = sum(int(source.get(kind, 0)) for kind in TRACKED_KINDS)
    total_mapped = sum(min(int(mapped.get(kind, 0)), int(source.get(kind, 0))) for kind in TRACKED_KINDS)
    if images_total:
        total_source += int(images_total)
        total_mapped += int(images_downloaded or 0)

    empty = _is_empty(document, total_source)
    content_chars = document.content_chars()

    report: dict[str, Any] = {
        # Carried forward so audit() is idempotent: without it a re-audit loses
        # the image accounting and a failing ratio silently becomes 1.0.
        "assets": figure_assets,
        "source_counts": source,
        "mapped_counts": mapped,
        "unmapped": unmapped,
        "min_ratio": min_ratio,
        "ir_available": ir_available,
        "empty": empty,
        "content_chars": content_chars,
    }

    if not ir_available:
        # Extractors that emit markdown only (no structured IR) cannot make a
        # fidelity claim. Report no ratio rather than a meaningless 1.0.
        report["ratio"] = None
        report["ok"] = False
        report["reason"] = "extractor produced no structured IR"
        document.coverage = report
        return report

    if empty:
        report["ratio"] = None
        report["ok"] = False
        report["reason"] = f"extraction produced an empty document ({content_chars} chars)"
        document.coverage = report
        return report

    # Nothing to account for (no math, figures, tables, bibliography or notes)
    # is only meaningful once we know the body itself is non-empty.
    ratio = 1.0 if total_source == 0 else total_mapped / total_source
    report["ratio"] = round(ratio, 4)
    report["ok"] = ratio >= min_ratio
    if not report["ok"]:
        report["reason"] = f"coverage {ratio:.4f} below {min_ratio:.2f}"
    document.coverage = report
    return report


