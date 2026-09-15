from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourceAnchor:
    element_id: str | None = None
    tag: str | None = None
    classes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"element_id": self.element_id, "tag": self.tag, "classes": list(self.classes)}


@dataclass
class Inline:
    kind: str
    text: str = ""
    tex: str | None = None
    display: bool = False
    href: str | None = None
    keys: list[str] = field(default_factory=list)
    label: str | None = None
    src: str | None = None
    alt: str | None = None
    children: list[Inline] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.text:
            payload["text"] = self.text
        if self.tex is not None:
            payload["tex"] = self.tex
        if self.display:
            payload["display"] = True
        if self.href:
            payload["href"] = self.href
        if self.keys:
            payload["keys"] = list(self.keys)
        if self.label is not None:
            payload["label"] = self.label
        if self.src:
            payload["src"] = self.src
        if self.alt:
            payload["alt"] = self.alt
        if self.children:
            payload["children"] = [child.to_dict() for child in self.children]
        return payload


@dataclass
class ImageRef:
    src: str
    alt: str = ""
    local: str | None = None
    sha256: str | None = None
    source_url: str | None = None
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"src": self.src}
        if self.alt:
            payload["alt"] = self.alt
        if self.local:
            payload["local"] = self.local
        if self.sha256:
            payload["sha256"] = self.sha256
        if self.source_url:
            payload["source_url"] = self.source_url
        if self.page is not None:
            payload["page"] = self.page
        return payload


@dataclass
class Cell:
    inlines: list[Inline] = field(default_factory=list)
    rowspan: int = 1
    colspan: int = 1
    header: bool = False
    align: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"inlines": [i.to_dict() for i in self.inlines]}
        if self.rowspan != 1:
            payload["rowspan"] = self.rowspan
        if self.colspan != 1:
            payload["colspan"] = self.colspan
        if self.header:
            payload["header"] = True
        if self.align:
            payload["align"] = self.align
        return payload


@dataclass
class Table:
    id: str
    label: str | None = None
    caption: list[Inline] = field(default_factory=list)
    header_rows: int = 0
    rows: list[list[Cell]] = field(default_factory=list)
    html: str = ""
    has_spans: bool = False
    anchor: SourceAnchor | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "rows": [[cell.to_dict() for cell in row] for row in self.rows],
            "header_rows": self.header_rows,
        }
        if self.label:
            payload["label"] = self.label
        if self.caption:
            payload["caption"] = [i.to_dict() for i in self.caption]
        if self.html:
            payload["html"] = self.html
        if self.has_spans:
            payload["has_spans"] = True
        if self.anchor:
            payload["anchor"] = self.anchor.to_dict()
        return payload


@dataclass
class Figure:
    id: str
    label: str | None = None
    caption: list[Inline] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    anchor: SourceAnchor | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "images": [img.to_dict() for img in self.images],
        }
        if self.label:
            payload["label"] = self.label
        if self.caption:
            payload["caption"] = [i.to_dict() for i in self.caption]
        if self.anchor:
            payload["anchor"] = self.anchor.to_dict()
        return payload


@dataclass
class Block:
    kind: str
    id: str = ""
    level: int = 0
    inlines: list[Inline] = field(default_factory=list)
    tex: str = ""
    text: str = ""
    language: str | None = None
    ordered: bool = False
    items: list[Block] = field(default_factory=list)
    table: Table | None = None
    figure: Figure | None = None
    anchor: SourceAnchor | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.id:
            payload["id"] = self.id
        if self.level:
            payload["level"] = self.level
        if self.inlines:
            payload["inlines"] = [i.to_dict() for i in self.inlines]
        if self.tex:
            payload["tex"] = self.tex
        if self.text:
            payload["text"] = self.text
        if self.language:
            payload["language"] = self.language
        if self.ordered:
            payload["ordered"] = True
        if self.items:
            payload["items"] = [item.to_dict() for item in self.items]
        if self.table:
            payload["table"] = self.table.to_dict()
        if self.figure:
            payload["figure"] = self.figure.to_dict()
        if self.anchor:
            payload["anchor"] = self.anchor.to_dict()
        if self.meta:
            payload["meta"] = dict(self.meta)
        return payload


@dataclass
class BibliographyEntry:
    id: str
    label: str
    inlines: list[Inline] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id, "label": self.label}
        if self.inlines:
            payload["inlines"] = [i.to_dict() for i in self.inlines]
        if self.raw_text:
            payload["raw_text"] = self.raw_text
        return payload


@dataclass
class Document:
    schema_version: int
    key: str
    source_kind: str
    source_url: str
    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    bibliography: list[BibliographyEntry] = field(default_factory=list)
    footnotes: list[Block] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "key": self.key,
            "source_kind": self.source_kind,
            "source_url": self.source_url,
            "title": self.title,
            "blocks": [block.to_dict() for block in self.blocks],
            "bibliography": [entry.to_dict() for entry in self.bibliography],
            "footnotes": [note.to_dict() for note in self.footnotes],
            "coverage": self.coverage,
        }

    def iter_sections(self) -> list[dict[str, Any]]:
        """Derive a flat outline of headings with the index range they cover."""
        sections: list[dict[str, Any]] = []
        for index, block in enumerate(self.blocks):
            if block.kind != "heading":
                continue
            if sections:
                sections[-1]["end"] = index
            sections.append(
                {
                    "id": block.id,
                    "level": block.level,
                    "title": _plain_text(block.inlines),
                    "start": index,
                    "end": len(self.blocks),
                }
            )
        if sections:
            sections[-1]["end"] = len(self.blocks)
        return sections

    def content_chars(self) -> int:
        """Total plain-text length of the body, used to detect empty extractions."""
        total = 0
        for block in self.blocks:
            total += len(block.text) + len(block.tex) + len(_plain_text(block.inlines))
            for item in block.items:
                total += len(item.text) + len(_plain_text(item.inlines))
        return total


def _plain_text(inlines: list[Inline]) -> str:
    parts: list[str] = []
    for inline in inlines:
        parts.append(_plain_inline(inline))
    return "".join(parts).strip()


def _plain_inline(inline: Inline) -> str:
    if inline.kind == "text":
        return inline.text
    if inline.kind == "math":
        return inline.tex or inline.text
    if inline.kind in {"code", "label"}:
        return inline.text
    if inline.kind == "cite":
        return f"[{', '.join(inline.keys) or inline.label or ''}]"
    if inline.kind == "note_ref":
        return f"[{inline.label or ''}]"
    if inline.kind == "image":
        return inline.alt or ""
    if inline.children:
        return "".join(_plain_inline(child) for child in inline.children)
    return inline.text


def document_to_dict(document: Document) -> dict[str, Any]:
    return document.to_dict()


def block_to_dict(block: Block) -> dict[str, Any]:
    return asdict(block)
