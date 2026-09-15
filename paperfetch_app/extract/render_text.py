from __future__ import annotations

import re
from typing import Any

from .ir import Block, Document, Inline


def strip_markdown(text: str) -> str:
    """Best-effort markdown to plain text for PDF-extracted content."""
    result = re.sub(r"```[^\n]*\n?(.*?)```", r"\1", text, flags=re.DOTALL)
    result = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", result)
    result = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", result)
    result = re.sub(r"^#{1,6}\s*", "", result, flags=re.MULTILINE)
    result = re.sub(r"[*_`>]", "", result)
    return result.strip() + "\n"


def render_text(document: Document, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    parts: list[str] = []
    if document.title:
        parts.append(document.title)
        parts.append("=" * len(document.title))
    for block in document.blocks:
        rendered = _render_block(block)
        if rendered:
            parts.append(rendered)
    if document.footnotes:
        parts.append("Notes:")
        for note in document.footnotes:
            parts.append(f"[{note.id}] {_render_inlines(note.inlines).strip()}")
    return "\n\n".join(parts).strip() + "\n"


def _render_block(block: Block) -> str:
    if block.kind == "heading":
        return _render_inlines(block.inlines).strip()
    if block.kind == "math":
        label = block.meta.get("label")
        suffix = f" {label}" if label else ""
        return block.tex.strip() + suffix
    if block.kind == "code":
        return block.text
    if block.kind == "list":
        return _render_list(block, 0)
    if block.kind == "table" and block.table is not None:
        return _render_table(block)
    if block.kind == "figure" and block.figure is not None:
        figure = block.figure
        caption = _render_inlines(figure.caption).strip()
        images = ", ".join(image.local or image.src for image in figure.images)
        pieces = [piece for piece in (figure.label, caption, images) if piece]
        return " | ".join(pieces)
    if block.kind == "raw":
        return block.text
    return _render_inlines(block.inlines).strip()


def _render_list(block: Block, indent: int) -> str:
    lines: list[str] = []
    prefix = " " * indent
    for index, item in enumerate(block.items, start=1):
        marker = f"{index}." if block.ordered else "-"
        lines.append(f"{prefix}{marker} {_render_inlines(item.inlines).strip()}")
        for child in item.items:
            lines.append(_render_list(child, indent + 2))
    return "\n".join(lines)


def _render_table(block: Block) -> str:
    table = block.table
    assert table is not None
    lines: list[str] = []
    caption = _render_inlines(table.caption).strip()
    if table.label or caption:
        lines.append(f"{table.label or 'Table'}: {caption}".strip())
    for row in table.rows:
        lines.append("\t".join(_render_inlines(cell.inlines).strip() for cell in row))
    return "\n".join(lines)


def _render_inlines(inlines: list[Inline]) -> str:
    return "".join(_render_inline(inline) for inline in inlines)


def _render_inline(inline: Inline) -> str:
    if inline.kind == "text":
        return inline.text
    if inline.kind == "math":
        return inline.tex or inline.text or ""
    if inline.kind == "cite":
        return f"[{', '.join(inline.keys) or inline.label or ''}]"
    if inline.kind == "note_ref":
        return f"[{inline.label}]"
    if inline.kind == "code":
        return inline.text
    if inline.kind == "image":
        return inline.alt or inline.src or ""
    if inline.children:
        return _render_inlines(inline.children)
    return inline.text
