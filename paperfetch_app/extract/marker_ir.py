"""Build a structured Document IR from marker's JSON output.

marker runs a layout model over the PDF and emits a block tree (section
headers, figures, tables, captions, equations). Rendering that tree to markdown
before paperfetch sees it throws the structure away, so we consume the JSON and
map it onto the same IR the arXiv HTML extractor produces.

The schema is normalized defensively: marker's JSON has shifted between
releases, so every field is read through a helper that tolerates absence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .ir import (
    Block,
    Cell,
    Document,
    Figure,
    ImageRef,
    Inline,
    SourceAnchor,
    Table,
)

SCHEMA_VERSION = 1

# marker block_type names, grouped by how they map onto the IR.
HEADING_TYPES = {"SectionHeader"}
TEXT_TYPES = {"Text", "TextInlineMath", "ComplexRegion", "Handwriting"}
FIGURE_TYPES = {"Figure", "Picture"}
FIGURE_GROUP_TYPES = {"FigureGroup", "PictureGroup"}
TABLE_TYPES = {"Table"}
TABLE_GROUP_TYPES = {"TableGroup"}
EQUATION_TYPES = {"Equation"}
CAPTION_TYPES = {"Caption"}
CODE_TYPES = {"Code"}
LIST_TYPES = {"ListGroup"}
LIST_ITEM_TYPES = {"ListItem"}
FOOTNOTE_TYPES = {"Footnote"}
REFERENCE_TYPES = {"Reference"}
# Running headers/footers and page furniture are not document content.
SKIP_TYPES = {"PageHeader", "PageFooter", "TableOfContents", "Form"}


def _children(node: Any) -> list[dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    kids = node.get("children")
    return [kid for kid in kids if isinstance(kid, dict)] if isinstance(kids, list) else []


def _block_type(node: dict[str, Any]) -> str:
    return str(node.get("block_type") or node.get("type") or "")


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id") or "")


def _html(node: dict[str, Any]) -> str:
    return str(node.get("html") or "")


def _text_of(node: dict[str, Any]) -> str:
    html = _html(node)
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def _inlines(text: str) -> list[Inline]:
    return [Inline(kind="text", text=text)] if text else []


def _anchor(node: dict[str, Any]) -> SourceAnchor:
    return SourceAnchor(element_id=_node_id(node) or None, tag=_block_type(node).lower() or None)


def _heading_level(node: dict[str, Any]) -> int:
    """This heading's own level, else the level of the emitted <hN> tag.

    section_hierarchy maps level -> block id for the hierarchy *enclosing* this
    heading, so its deepest key is the current nesting depth, not this
    heading's level. The entry pointing back at this node is the right one.
    """
    hierarchy = node.get("section_hierarchy")
    node_id = _node_id(node)
    if isinstance(hierarchy, dict) and node_id:
        for level, block_id in hierarchy.items():
            if block_id == node_id:
                try:
                    return max(1, min(6, int(level)))
                except (TypeError, ValueError):
                    break
    soup = BeautifulSoup(_html(node), "lxml")
    for level in range(1, 7):
        if soup.find(f"h{level}") is not None:
            return level
    return 2


def _iter_images(node: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield (name, base64) pairs from whichever key this marker build uses."""
    images = node.get("images")
    if isinstance(images, dict):
        for name, payload in images.items():
            if isinstance(payload, str) and payload:
                yield str(name), payload
    single = node.get("image")
    if isinstance(single, str) and single:
        yield f"{_node_id(node) or 'image'}", single


def _safe_name(raw: str, suffix: str = ".png") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw).strip("-")
    cleaned = cleaned[:60] or "image"
    return cleaned if cleaned.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) else f"{cleaned}{suffix}"


def _parse_table_html(html: str) -> tuple[list[list[Cell]], int, bool]:
    """Parse marker's table HTML, preserving rowspan/colspan.

    Mirrors the span handling in the arXiv extractor. It stays separate because
    that one builds rich inlines (inline math, citations) from LaTeXML markup,
    while marker emits plain cell text.
    """
    soup = BeautifulSoup(html, "lxml")
    table_tag = soup.find("table") or soup
    rows: list[list[Cell]] = []
    for tr in table_tag.find_all("tr"):
        row: list[Cell] = []
        for cell_tag in tr.find_all(["td", "th"]):
            try:
                rowspan = max(1, int(cell_tag.get("rowspan", 1)))
            except (TypeError, ValueError):
                rowspan = 1
            try:
                colspan = max(1, int(cell_tag.get("colspan", 1)))
            except (TypeError, ValueError):
                colspan = 1
            row.append(
                Cell(
                    inlines=_inlines(cell_tag.get_text(" ", strip=True)),
                    rowspan=rowspan,
                    colspan=colspan,
                    header=(cell_tag.name or "").lower() == "th",
                )
            )
        if row:
            rows.append(row)

    thead = table_tag.find("thead")
    if thead is not None:
        header_rows = len(thead.find_all("tr"))
    elif rows and all(cell.header for cell in rows[0]):
        header_rows = 1
    else:
        header_rows = 0

    has_spans = any(c.rowspan != 1 or c.colspan != 1 for row in rows for c in row) or header_rows > 1
    return rows, header_rows, has_spans


class _MarkerAdapter:
    def __init__(self, key: str, source_url: str, figures_dir: Path) -> None:
        self.key = key
        self.source_url = source_url
        self.figures_dir = figures_dir
        self.blocks: list[Block] = []
        self.footnotes: list[Block] = []
        self.source_counts: dict[str, int] = {"math": 0, "figures": 0, "tables": 0, "bibitems": 0, "notes": 0}
        self.mapped_counts: dict[str, int] = {"math": 0, "figures": 0, "tables": 0, "bibitems": 0, "notes": 0}
        self.unmapped: list[dict[str, Any]] = []
        self.images_total = 0
        self.images_written = 0
        self._counter = 0

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    # ---------------------------------------------------------------- images
    def _write_images(self, node: dict[str, Any]) -> list[ImageRef]:
        refs: list[ImageRef] = []
        for name, payload in _iter_images(node):
            self.images_total += 1
            try:
                raw = base64.b64decode(payload, validate=False)
            except (binascii.Error, ValueError) as exc:
                self.unmapped.append({"kind": "figure_images", "id": name, "error": str(exc)})
                continue
            if not raw:
                self.unmapped.append({"kind": "figure_images", "id": name, "error": "empty image payload"})
                continue
            self.figures_dir.mkdir(parents=True, exist_ok=True)
            filename = _safe_name(name)
            destination = self.figures_dir / filename
            destination.write_bytes(raw)
            self.images_written += 1
            refs.append(
                ImageRef(
                    src=name,
                    local=f"figures/{filename}",
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
            )
        return refs

    # ---------------------------------------------------------------- floats
    def _figure(self, node: dict[str, Any], caption: str, label: str | None) -> None:
        self.source_counts["figures"] += 1
        images = self._write_images(node)
        for child in _children(node):
            if _block_type(child) in FIGURE_TYPES:
                images.extend(self._write_images(child))
        figure = Figure(
            id=_node_id(node) or self._next_id("figure"),
            label=label,
            caption=_inlines(caption),
            images=images,
            anchor=_anchor(node),
        )
        self.mapped_counts["figures"] += 1
        self.blocks.append(Block(kind="figure", id=figure.id, figure=figure, anchor=figure.anchor))

    def _table(self, node: dict[str, Any], caption: str, label: str | None) -> None:
        self.source_counts["tables"] += 1
        html = _html(node)
        if not html:
            for child in _children(node):
                if _block_type(child) in TABLE_TYPES:
                    html = _html(child)
                    break
        if not html:
            self.unmapped.append({"kind": "tables", "id": _node_id(node), "error": "no table html"})
            return
        rows, header_rows, has_spans = _parse_table_html(html)
        table = Table(
            id=_node_id(node) or self._next_id("table"),
            label=label,
            caption=_inlines(caption),
            header_rows=header_rows,
            rows=rows,
            html=html,
            has_spans=has_spans,
            anchor=_anchor(node),
        )
        self.mapped_counts["tables"] += 1
        self.blocks.append(Block(kind="table", id=table.id, table=table, anchor=table.anchor))

    @staticmethod
    def _caption_for(node: dict[str, Any]) -> tuple[str, str | None]:
        """A grouped float carries its Caption as a sibling child."""
        for child in _children(node):
            if _block_type(child) in CAPTION_TYPES:
                text = _text_of(child)
                label = None
                lowered = text.lower()
                for prefix in ("figure", "fig.", "table"):
                    if lowered.startswith(prefix):
                        label = text.split(":")[0].strip() or None
                        break
                return text, label
        return "", None

    # ------------------------------------------------------------------ walk
    def visit(self, node: dict[str, Any]) -> None:
        kind = _block_type(node)

        if kind in SKIP_TYPES:
            return

        if kind in FIGURE_GROUP_TYPES or kind in TABLE_GROUP_TYPES:
            caption, label = self._caption_for(node)
            if kind in FIGURE_GROUP_TYPES:
                self._figure(node, caption, label)
            else:
                self._table(node, caption, label)
            return

        if kind in FIGURE_TYPES:
            self._figure(node, "", None)
            return

        if kind in TABLE_TYPES:
            self._table(node, "", None)
            return

        if kind in EQUATION_TYPES:
            self.source_counts["math"] += 1
            text = _text_of(node)
            if not text:
                self.unmapped.append({"kind": "math", "id": _node_id(node)})
                return
            self.mapped_counts["math"] += 1
            self.blocks.append(
                Block(kind="equation", id=_node_id(node) or self._next_id("eq"), tex=text, anchor=_anchor(node))
            )
            return

        if kind in HEADING_TYPES:
            self.blocks.append(
                Block(
                    kind="heading",
                    id=_node_id(node) or self._next_id("sec"),
                    level=_heading_level(node),
                    inlines=_inlines(_text_of(node)),
                    anchor=_anchor(node),
                )
            )
            return

        if kind in FOOTNOTE_TYPES:
            self.source_counts["notes"] += 1
            text = _text_of(node)
            if text:
                self.mapped_counts["notes"] += 1
                self.footnotes.append(
                    Block(kind="paragraph", id=_node_id(node) or self._next_id("note"), inlines=_inlines(text))
                )
            return

        if kind in REFERENCE_TYPES:
            self.source_counts["bibitems"] += 1
            text = _text_of(node)
            if text:
                self.mapped_counts["bibitems"] += 1
                self.blocks.append(
                    Block(kind="paragraph", id=_node_id(node) or self._next_id("ref"), inlines=_inlines(text))
                )
            return

        if kind in CODE_TYPES:
            text = _text_of(node)
            if text:
                self.blocks.append(Block(kind="code", id=_node_id(node) or self._next_id("code"), text=text))
            return

        if kind in LIST_ITEM_TYPES:
            text = _text_of(node)
            if text:
                self.blocks.append(
                    Block(kind="paragraph", id=_node_id(node) or self._next_id("li"), inlines=_inlines(text))
                )
            return

        if kind in TEXT_TYPES:
            text = _text_of(node)
            if text:
                self.blocks.append(
                    Block(kind="paragraph", id=_node_id(node) or self._next_id("p"), inlines=_inlines(text))
                )
            return

        # Containers (Document/Page/ListGroup) and anything unrecognized: recurse.
        for child in _children(node):
            self.visit(child)


def document_from_marker_json(
    payload: dict[str, Any],
    *,
    key: str,
    source_url: str,
    figures_dir: Path,
    title: str = "",
) -> Document:
    """Map marker's JSON block tree onto the Document IR."""
    adapter = _MarkerAdapter(key, source_url, figures_dir)
    adapter.visit(payload)

    document = Document(
        schema_version=SCHEMA_VERSION,
        key=key,
        source_kind="marker",
        source_url=source_url,
        title=title,
        blocks=adapter.blocks,
        footnotes=adapter.footnotes,
    )
    document.coverage = {
        "source_counts": dict(adapter.source_counts),
        "mapped_counts": dict(adapter.mapped_counts),
        "unmapped": adapter.unmapped,
        "ir_available": True,
        "assets": {
            "images_total": adapter.images_total,
            "images_downloaded": adapter.images_written,
        },
    }
    return document
