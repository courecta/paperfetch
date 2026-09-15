from __future__ import annotations

from typing import Any

from ..errors import CoverageError
from .ir import Document

DEFAULT_MIN_RATIO = 0.95
TRACKED_KINDS = ("math", "figures", "tables", "bibitems", "notes")


def audit(document: Document, *, min_ratio: float = DEFAULT_MIN_RATIO) -> dict[str, Any]:
    coverage = dict(document.coverage or {})
    source = coverage.get("source_counts", {}) or {}
    mapped = coverage.get("mapped_counts", {}) or {}
    unmapped = list(coverage.get("unmapped", []) or [])

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
    ratio = 1.0 if total_source == 0 else total_mapped / total_source

    report = {
        "source_counts": source,
        "mapped_counts": mapped,
        "unmapped": unmapped,
        "ratio": round(ratio, 4),
        "min_ratio": min_ratio,
        "ok": ratio >= min_ratio and not unmapped,
    }
    document.coverage = report
    return report


def ensure_coverage(document: Document, *, min_ratio: float = DEFAULT_MIN_RATIO) -> dict[str, Any]:
    report = audit(document, min_ratio=min_ratio)
    if report["ok"]:
        return report
    details = ", ".join(
        f"{item.get('kind')} x{item.get('missing', 1)}" for item in report["unmapped"][:6]
    ) or f"ratio {report['ratio']}"
    raise CoverageError(
        f"Extraction coverage {report['ratio']:.2f} below threshold {min_ratio:.2f} ({details})",
        report=report,
    )
