from __future__ import annotations

import re
from typing import Any

from .ir import Block, Document, Inline, Table

PIPE_TABLE_SAFE = re.compile(r"[|\n]")


def render_markdown(
    document: Document,
    *,
    include_frontmatter: bool = True,
    metadata: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    if include_frontmatter:
        parts.append(_frontmatter(document, metadata or {}))

    for block in document.blocks:
        rendered = _render_block(block)
        if rendered:
            parts.append(rendered)

    if document.footnotes:
        parts.append("")
        for note in document.footnotes:
            content = _render_inlines(note.inlines).strip()
            parts.append(f"[^{note.id}]: {content}")

    text = "\n\n".join(part.rstrip() for part in parts if part is not None)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip() + "\n"


def _frontmatter(document: Document, metadata: dict[str, Any]) -> str:
    title = document.title or str(metadata.get("title") or "")
    lines = ["---"]
    lines.append(f"title: {_yaml_string(title)}")
    lines.append(f"paperfetch_key: {document.key}")
    lines.append(f"source: {document.source_url}")
    lines.append(f"extractor: {document.source_kind}")
    for field in ("authors", "year", "venue", "doi", "arxiv_id"):
        value = metadata.get(field)
        if value:
            lines.append(f"{field}: {_yaml_string(value)}")
    lines.append("---")
    return "\n".join(lines)


def _yaml_string(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_yaml_string(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _render_block(block: Block) -> str:
    if block.kind == "heading":
        level = max(1, min(block.level, 6))
        return "#" * level + " " + _render_inlines(block.inlines).strip()
    if block.kind == "paragraph":
        return _render_inlines(block.inlines).strip()
    if block.kind == "math":
        return _render_math(block)
    if block.kind == "code":
        fence = _fence_for(block.text)
        language = block.language or ""
        return f"{fence}{language}\n{block.text}\n{fence}"
    if block.kind == "list":
        return _render_list(block, indent=0)
    if block.kind == "table" and block.table is not None:
        return _render_table(block.table)
    if block.kind == "figure" and block.figure is not None:
        return _render_figure(block)
    if block.kind == "rule":
        return "---"
    if block.kind == "raw":
        return block.text
    if block.inlines:
        return _render_inlines(block.inlines).strip()
    return ""


def _render_math(block: Block) -> str:
    tex = block.tex.strip()
    label = block.meta.get("label")
    if label:
        bare = str(label).strip().strip("()[]")
        if bare and "\\tag" not in tex:
            tex = f"{tex} \\tag{{{bare}}}"
    return "\\[\n" + tex + "\n\\]"


def _render_list(block: Block, indent: int) -> str:
    lines: list[str] = []
    prefix = " " * indent
    for index, item in enumerate(block.items, start=1):
        marker = f"{index}." if block.ordered else "-"
        text = _render_inlines(item.inlines).strip()
        lines.append(f"{prefix}{marker} {text}".rstrip())
        for child in item.items:
            nested = _render_list(child, indent + 2)
            if nested:
                lines.append(nested)
    return "\n".join(lines)


def _render_table(table: Table) -> str:
    parts: list[str] = []
    caption = _render_inlines(table.caption).strip()
    label = table.label or "Table"
    if caption:
        parts.append(f"**{label}.** {caption}")
    elif table.label:
        parts.append(f"**{table.label}**")
    parts.append(_table_body(table))
    return "\n\n".join(parts)


def _table_body(table: Table) -> str:
    simple = (
        not table.has_spans
        and table.header_rows <= 1
        and table.rows
        and all(len(row) == len(table.rows[0]) for row in table.rows)
    )
    if simple:
        return _pipe_table(table)
    if table.html:
        return table.html.strip()
    return _pipe_table(table)


def _pipe_table(table: Table) -> str:
    rows = [[_render_inlines(cell.inlines).strip() for cell in row] for row in table.rows]
    if not rows:
        return ""
    header_rows = table.header_rows or 1
    header = rows[0]
    body = rows[1:] if header_rows == 1 else rows
    col_count = max(len(row) for row in rows)
    header = header + [""] * (col_count - len(header))
    lines = ["| " + " | ".join(_escape_cell(cell) for cell in header) + " |"]
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for row in body:
        row = row + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(_escape_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    return PIPE_TABLE_SAFE.sub(lambda m: "\\|" if m.group(0) == "|" else " ", value)


def _render_figure(block: Block) -> str:
    figure = block.figure
    assert figure is not None
    lines: list[str] = []
    for image in figure.images:
        src = image.local or image.src
        alt = image.alt or figure.label or "figure"
        lines.append(f"![{alt}]({src})")
    caption = _render_inlines(figure.caption).strip()
    label = figure.label or "Figure"
    if caption:
        lines.append(f"**{label}.** {caption}")
    elif figure.label:
        lines.append(f"**{figure.label}**")
    if not lines:
        return f"**[{label}: no image asset captured]**"
    return "\n\n".join(lines)


def _render_inlines(inlines: list[Inline]) -> str:
    return "".join(_render_inline(inline) for inline in inlines)


def _render_inline(inline: Inline) -> str:
    kind = inline.kind
    if kind == "text":
        return inline.text
    if kind == "math":
        tex = (inline.tex or inline.text or "").strip()
        if not tex:
            return ""
        return f"${tex}$" if not inline.display else f"$$\n{tex}\n$$"
    if kind == "strong":
        return f"**{_render_inlines(inline.children).strip()}**"
    if kind == "emph":
        return f"*{_render_inlines(inline.children).strip()}*"
    if kind == "code":
        return _code_span(inline.text)
    if kind == "link":
        return f"[{_render_inlines(inline.children).strip() or inline.href}]({inline.href})"
    if kind == "cite":
        keys = ", ".join(f"@{key}" for key in inline.keys) if inline.keys else (inline.label or "")
        return f"[{keys}]"
    if kind == "note_ref":
        return f"[^{inline.label}]"
    if kind == "sup":
        return f"<sup>{_render_inlines(inline.children).strip()}</sup>"
    if kind == "sub":
        return f"<sub>{_render_inlines(inline.children).strip()}</sub>"
    if kind == "image":
        return f"![{inline.alt or ''}]({inline.src})"
    if kind == "raw":
        return inline.text
    if inline.children:
        return _render_inlines(inline.children)
    return inline.text


def _code_span(text: str) -> str:
    fence = "`" * (max(len(run) for run in re.findall(r"`+", text)) + 1 if re.findall(r"`+", text) else 1)
    return f"{fence}{text}{fence}"


def _fence_for(text: str) -> str:
    runs = re.findall(r"`{3,}", text)
    length = max((len(run) for run in runs), default=2) + 1
    return "`" * max(3, length)
