from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .bundle import BundlePaths
from .errors import DownloadError, NetworkError, PaperfetchError
from .extract.ir import Document
from .fetch.http import HttpClient
from .io_utils import ensure_dir, safe_slug, write_json_atomic

DEFAULT_MAX_ASSET_BYTES = 25 * 1024 * 1024
KNOWN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".pdf", ".eps"}
CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")


@dataclass
class AssetReport:
    images_total: int = 0
    images_downloaded: int = 0
    images_failed: int = 0
    images_reused: int = 0
    bytes_total: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "images_total": self.images_total,
            "images_downloaded": self.images_downloaded,
            "images_failed": self.images_failed,
            "images_reused": self.images_reused,
            "bytes_total": self.bytes_total,
            "failures": self.failures,
        }


def _extension_for(url: str, content_type: str | None = None) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if suffix in KNOWN_EXTENSIONS:
        return suffix
    if content_type:
        return CONTENT_TYPE_EXT.get(content_type.split(";")[0].strip().lower(), ".png")
    return ".png"


def ingest_figure_assets(
    document: Document,
    paths: BundlePaths,
    client: HttpClient,
    *,
    max_bytes: int = DEFAULT_MAX_ASSET_BYTES,
) -> AssetReport:
    """Download every figure image and rewrite IR references to local paths."""
    ensure_dir(paths.figures_dir)
    report = AssetReport()
    cache: dict[str, str] = {}

    for block in document.blocks:
        if block.kind != "figure" or block.figure is None:
            continue
        figure = block.figure
        for index, image in enumerate(figure.images, start=1):
            if image.src.startswith("data:"):
                continue
            report.images_total += 1
            cached = cache.get(image.src)
            if cached:
                image.local = cached
                report.images_downloaded += 1
                report.images_reused += 1
                continue

            slug = safe_slug(figure.id or block.id or "figure", max_length=60)
            filename = f"{slug}-{index}{_extension_for(image.src, None)}"
            target = paths.figures_dir / filename
            try:
                result = client.download(
                    image.src,
                    target,
                    expect_pdf=False,
                    max_bytes=max_bytes,
                    retries=2,
                    backoff=1.0,
                )
            except (DownloadError, NetworkError, PaperfetchError) as exc:
                report.images_failed += 1
                report.failures.append({"figure": figure.id, "src": image.src, "error": str(exc)})
                continue
            image.local = f"figures/{filename}"
            image.sha256 = result.sha256
            image.source_url = image.src
            report.images_downloaded += 1
            report.bytes_total += result.size
            cache[image.src] = image.local

    document.coverage.setdefault("assets", {})
    document.coverage["assets"] = report.to_dict()
    write_figure_manifest(document, paths)
    return report


def write_figure_manifest(document: Document, paths: BundlePaths) -> None:
    figures: list[dict[str, Any]] = []
    for block in document.blocks:
        if block.kind != "figure" or block.figure is None:
            continue
        figure = block.figure
        figures.append(
            {
                "id": figure.id,
                "label": figure.label,
                "caption": _plain_inlines(figure.caption),
                "images": [
                    {
                        "src": image.src,
                        "local": image.local,
                        "sha256": image.sha256,
                        "alt": image.alt,
                        "page": image.page,
                    }
                    for image in figure.images
                ],
            }
        )
    write_json_atomic(paths.figures_manifest(), {"figures": figures})


def write_table_sidecars(document: Document, paths: BundlePaths) -> None:
    """Write canonical HTML plus span-expanded CSV for every table."""
    ensure_dir(paths.tables_dir)
    manifest: list[dict[str, Any]] = []
    for block in document.blocks:
        if block.kind != "table" or block.table is None:
            continue
        table = block.table
        table_dict = table.to_dict()
        html_path = paths.tables_dir / f"{safe_slug(table.id, max_length=60)}.html"
        html_path.write_text(render_table_html(table_dict), encoding="utf-8")
        csv_path = paths.tables_dir / f"{safe_slug(table.id, max_length=60)}.csv"
        csv_path.write_text(table_to_csv(table_dict), encoding="utf-8")
        manifest.append(
            {
                "id": table.id,
                "label": table.label,
                "caption": _plain_inlines(table.caption),
                "html": f"tables/{html_path.name}",
                "csv": f"tables/{csv_path.name}",
                "rows": len(table.rows),
                "header_rows": table.header_rows,
            }
        )
    write_json_atomic(paths.tables_manifest(), {"tables": manifest})


def render_table_html(table: dict[str, Any]) -> str:
    raw_html = table.get("html")
    if raw_html:
        return str(raw_html)

    def _row(cells: list[dict[str, Any]], header: bool) -> str:
        tag = "th" if header else "td"
        parts = []
        for cell in cells:
            attrs = ""
            if int(cell.get("rowspan", 1)) != 1:
                attrs += f' rowspan="{cell["rowspan"]}"'
            if int(cell.get("colspan", 1)) != 1:
                attrs += f' colspan="{cell["colspan"]}"'
            parts.append(f"<{tag}{attrs}>{_escape_html(_plain_inlines(cell.get('inlines') or []))}</{tag}>")
        return "<tr>" + "".join(parts) + "</tr>"

    header_rows = int(table.get("header_rows") or 0)
    rows_html = [
        _row(row, index < header_rows) for index, row in enumerate(table.get("rows") or [])
    ]
    return f"<table>{''.join(rows_html)}</table>"


def _escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def table_to_csv(table: dict[str, Any]) -> str:
    import csv
    import io

    grid: dict[tuple[int, int], str] = {}
    max_col = 0
    for row_index, row in enumerate(table.get("rows") or []):
        col = 0
        for cell in row:
            while (row_index, col) in grid:
                col += 1
            text = _plain_inlines(cell.get("inlines") or []).replace("\n", " ").strip()
            for row_offset in range(max(1, int(cell.get("rowspan", 1)))):
                for col_offset in range(max(1, int(cell.get("colspan", 1)))):
                    grid[(row_index + row_offset, col + col_offset)] = text
            col += max(1, int(cell.get("colspan", 1)))
        max_col = max(max_col, col)

    row_count = max((row for row, _ in grid), default=-1) + 1
    output = io.StringIO()
    writer = csv.writer(output)
    for row_index in range(row_count):
        writer.writerow([grid.get((row_index, col), "") for col in range(max_col)])
    return output.getvalue()


def _plain_inlines(inlines: list[Any]) -> str:
    parts: list[str] = []
    for inline in inlines:
        if isinstance(inline, dict):
            kind = inline.get("kind")
            text = inline.get("text")
            tex = inline.get("tex")
            keys = inline.get("keys")
            children = inline.get("children")
        else:
            kind = inline.kind
            text = inline.text
            tex = inline.tex
            keys = inline.keys
            children = inline.children
        if kind == "text":
            parts.append(str(text or ""))
        elif kind == "math":
            parts.append(f"${tex}$" if tex else "")
        elif kind == "cite":
            parts.append("[" + ", ".join(str(key) for key in (keys or [])) + "]")
        elif children:
            parts.append(_plain_inlines(children))
        elif tex:
            parts.append(str(tex))
        else:
            parts.append(str(text or ""))
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def ingest_marker_output(
    markdown_text: str,
    markdown_dir: Path,
    paths: BundlePaths,
) -> tuple[str, AssetReport]:
    """Copy images referenced by marker output into the bundle and rewrite links."""
    ensure_dir(paths.figures_dir)
    report = AssetReport()
    stem = markdown_dir.name
    cache: dict[str, str] = {}

    def _resolve(rel_path: str) -> Path | None:
        candidates = [
            markdown_dir / rel_path,
            markdown_dir / Path(rel_path).name,
            markdown_dir / f"{stem}_images" / Path(rel_path).name,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        try:
            for candidate in markdown_dir.rglob(Path(rel_path).name):
                if candidate.is_file():
                    return candidate
        except OSError:
            return None
        return None

    def _replace(match: re.Match[str]) -> str:
        alt, target, title = match.group(1), match.group(2), match.group(3) or ""
        if target.startswith(("http://", "https://", "data:")):
            return match.group(0)
        if target in cache:
            return f"![{alt}]({cache[target]}{title})"
        resolved = _resolve(target)
        report.images_total += 1
        if resolved is None:
            report.images_failed += 1
            report.failures.append({"src": target, "error": "marker asset not found"})
            return match.group(0)
        filename = f"{safe_slug(stem, max_length=40)}-{len(cache) + 1}{resolved.suffix.lower() or '.png'}"
        destination = paths.figures_dir / filename
        if not destination.exists():
            destination.write_bytes(resolved.read_bytes())
        local = f"figures/{filename}"
        cache[target] = local
        report.images_downloaded += 1
        report.bytes_total += destination.stat().st_size
        return f"![{alt}]({local}{title})"

    rewritten = IMAGE_LINK_RE.sub(_replace, markdown_text)
    return rewritten, report
