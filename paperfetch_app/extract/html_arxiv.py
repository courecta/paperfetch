from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from ..fetch.http import HttpClient
from .ir import (
    BibliographyEntry,
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
ARXIV_HTML_URL = "https://arxiv.org/html/{paper_id}"

_WS_RE = re.compile(r"\s+")


def fetch_arxiv_html(
    paper_id: str,
    timeout_sec: int = 60,
    retries: int = 2,
    backoff: float = 1.5,
    client: HttpClient | None = None,
) -> str:
    """Download the arXiv HTML (LaTeXML) rendering of a paper."""
    url = ARXIV_HTML_URL.format(paper_id=paper_id)
    owns_client = client is None
    client = client or HttpClient(timeout=timeout_sec, retries=retries, backoff=backoff)
    try:
        return client.get_text(url, timeout=timeout_sec, retries=retries, backoff=backoff)
    finally:
        if owns_client:
            client.close()


def _latex_from_math(math_tag: Tag) -> str | None:
    for attribute in ("alttext", "tex", "data-semantic-latex"):
        value = math_tag.get(attribute)
        if value:
            return str(value)
    annotation = math_tag.find("annotation", encoding="application/x-tex")
    if annotation and annotation.string:
        return str(annotation.string)
    return None


def _math_text_fallback(math_tag: Tag) -> str:
    text = math_tag.get("text")
    if text:
        return str(text)
    return math_tag.get_text(" ", strip=True)


def _clean_label(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s*:\s*$", "", value.strip()).strip()
    return cleaned or None


def _classes(tag: Tag) -> set[str]:
    raw = tag.get("class") or []
    if isinstance(raw, str):
        raw = raw.split()
    return {str(item) for item in raw}


def _best_srcset(value: str) -> str | None:
    best: tuple[float, str] | None = None
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        url = pieces[0]
        weight = 1.0
        if len(pieces) > 1:
            descriptor = pieces[1]
            try:
                weight = float(descriptor.rstrip("wx"))
            except ValueError:
                weight = 1.0
        if best is None or weight > best[0]:
            best = (weight, url)
    return best[1] if best else None


class LatexmlConverter:
    """Convert arXiv LaTeXML HTML into the paperfetch document IR."""

    def __init__(
        self,
        *,
        key: str,
        source_url: str,
        paper_id: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.key = key
        self.source_url = source_url
        self.paper_id = paper_id
        self.base_url = base_url or source_url or "https://arxiv.org/"
        self.blocks: list[Block] = []
        self.bibliography: list[BibliographyEntry] = []
        self.footnotes: list[Block] = []
        self.source_counts: Counter[str] = Counter()
        self.mapped_counts: Counter[str] = Counter()
        self.unmapped: list[dict[str, Any]] = []
        self._block_seq = 0
        self._footnote_seq = 0
        self._seen_ids: set[str] = set()

    # ------------------------------------------------------------------ API
    def convert(self, html_text: str) -> Document:
        soup = BeautifulSoup(html_text, "lxml")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        title = self._extract_title(soup)
        main = (
            soup.find("article", class_=lambda c: c and "ltx_document" in c)
            or soup.find("article")
            or soup.find("main")
            or soup.find("div", class_="ltx_page_content")
            or soup.body
            or soup
        )
        for child in list(main.children):
            self._convert_block(child)

        self._count_source_elements(main)
        coverage = self._coverage_report()
        return Document(
            schema_version=SCHEMA_VERSION,
            key=self.key,
            source_kind="arxiv_html",
            source_url=self.source_url,
            title=title,
            blocks=self.blocks,
            bibliography=self.bibliography,
            footnotes=self.footnotes,
            coverage=coverage,
        )

    # ------------------------------------------------------------- internals
    def _next_id(self, prefix: str) -> str:
        while True:
            self._block_seq += 1
            candidate = f"{prefix}-{self._block_seq}"
            if candidate not in self._seen_ids:
                self._seen_ids.add(candidate)
                return candidate

    def _block_id(self, tag: Tag, prefix: str) -> str:
        element_id = tag.get("id")
        if element_id and element_id not in self._seen_ids:
            self._seen_ids.add(str(element_id))
            return str(element_id)
        return self._next_id(prefix)

    @staticmethod
    def _anchor(tag: Tag) -> SourceAnchor:
        return SourceAnchor(element_id=tag.get("id"), tag=tag.name, classes=sorted(_classes(tag)))

    def _extract_title(self, soup: BeautifulSoup) -> str:
        heading = soup.find("h1", class_=lambda c: c and "ltx_title_document" in c)
        if heading:
            return heading.get_text(" ", strip=True)
        if soup.title:
            text = soup.title.get_text(strip=True)
            text = re.sub(r"^\[[^\]]*\]\s*", "", text).strip()
            return text
        return ""

    # ---------------------------------------------------------- block level
    def _convert_block(self, node: Any) -> None:
        if isinstance(node, NavigableString):
            text = str(node).strip()
            if text:
                self.blocks.append(Block(kind="paragraph", inlines=[Inline(kind="text", text=text)]))
            return
        if not isinstance(node, Tag):
            return

        name = (node.name or "").lower()
        classes = _classes(node)

        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._append_heading(node, int(name[1]))
            return

        if name == "p":
            inlines = self._inlines(node)
            if _has_content(inlines):
                self.blocks.append(
                    Block(kind="paragraph", id=self._block_id(node, "para"), inlines=inlines, anchor=self._anchor(node))
                )
            return

        if name == "section":
            if "ltx_bibliography" in classes:
                self._parse_bibliography(node)
            for child in list(node.children):
                self._convert_block(child)
            return

        if name == "table":
            if any(cls.startswith("ltx_equation") or cls.startswith("ltx_eqn") for cls in classes):
                if self._append_equation_blocks(node):
                    return
            self._append_table(node)
            return

        if name == "figure":
            if "ltx_table" in classes:
                self._convert_table_float(node)
                return
            if any(cls.startswith("ltx_equation") or cls.startswith("ltx_eqn") for cls in classes) or (
                node.find("table", class_=lambda c: c and any(str(x).startswith("ltx_eqn") for x in c)) is not None
                and node.find("img") is None
            ):
                inner = node.find("table")
                if inner is not None and self._append_equation_blocks(inner):
                    return
            self._append_figure(node)
            return

        if name in ("ul", "ol"):
            if "ltx_biblist" in classes:
                return
            self._append_list(node)
            return

        if name == "pre":
            self._append_code(node)
            return

        if name == "math":
            tex = _latex_from_math(node) or _math_text_fallback(node)
            self.source_counts["math"] += 1
            if tex:
                self.mapped_counts["math"] += 1
            self.blocks.append(
                Block(
                    kind="math",
                    id=self._block_id(node, "eq"),
                    tex=tex,
                    meta={"display": True, "label": None},
                    anchor=self._anchor(node),
                )
            )
            return

        if name == "hr":
            self.blocks.append(Block(kind="rule", id=self._next_id("rule")))
            return

        if name in ("div", "span", "header", "footer", "aside", "main", "article", "dl", "dd", "dt", "blockquote"):
            for child in list(node.children):
                self._convert_block(child)
            return

        # Unknown block-level element: preserve its text rather than dropping it.
        text = node.get_text(" ", strip=True)
        if text:
            inlines = self._inlines(node)
            if _has_content(inlines):
                self.blocks.append(Block(kind="paragraph", id=self._next_id("para"), inlines=inlines))
            else:
                self.blocks.append(Block(kind="paragraph", id=self._next_id("para"), inlines=[Inline(kind="text", text=text)]))

    def _append_heading(self, node: Tag, level: int) -> None:
        inlines = self._inlines(node)
        if not _has_content(inlines):
            return
        self.blocks.append(
            Block(
                kind="heading",
                id=self._block_id(node, "sec"),
                level=level,
                inlines=inlines,
                anchor=self._anchor(node),
            )
        )

    def _append_list(self, node: Tag) -> None:
        items: list[Block] = []
        for li in node.find_all("li", recursive=False):
            nested = li.find_all(["ul", "ol"], recursive=False)
            inlines = self._inlines_without(li, nested)
            item = Block(kind="list_item", id=self._block_id(li, "item"), inlines=inlines)
            for sub in nested:
                sub_block = self._list_to_block(sub)
                if sub_block:
                    item.items.append(sub_block)
            if _has_content(inlines) or item.items:
                items.append(item)
        if items:
            self.blocks.append(
                Block(
                    kind="list",
                    id=self._block_id(node, "list"),
                    ordered=(node.name or "").lower() == "ol",
                    items=items,
                    anchor=self._anchor(node),
                )
            )

    def _list_to_block(self, node: Tag) -> Block | None:
        before = len(self.blocks)
        self._append_list(node)
        if len(self.blocks) > before:
            block = self.blocks.pop()
            return block
        return None

    def _append_code(self, node: Tag) -> None:
        text = node.get_text()
        language = None
        for cls in _classes(node):
            if cls.startswith("language-"):
                language = cls[len("language-") :]
        code_tag = node.find("code")
        if code_tag:
            for cls in _classes(code_tag):
                if cls.startswith("language-"):
                    language = cls[len("language-") :]
        self.blocks.append(
            Block(
                kind="code",
                id=self._block_id(node, "code"),
                text=text.rstrip("\n"),
                language=language,
                anchor=self._anchor(node),
            )
        )

    # ---------------------------------------------------------- equations
    def _append_equation_blocks(self, table: Tag) -> bool:
        bodies = table.find_all("tbody", recursive=False)
        units: list[Tag] = list(bodies) if bodies else list(table.find_all("tr", recursive=False))
        appended = False
        for unit in units:
            rows = list(unit.find_all("tr", recursive=False)) if unit.name == "tbody" else [unit]
            row_texts: list[str] = []
            label: str | None = None
            math_count = 0
            for row in rows:
                parts: list[str] = []
                for cell in row.find_all(["td", "th"], recursive=False):
                    cell_classes = _classes(cell)
                    if "ltx_eqn_eqno" in cell_classes or "ltx_eqn_number" in cell_classes:
                        label = label or _clean_label(cell.get_text(" ", strip=True))
                        continue
                    math_tags = cell.find_all("math")
                    if not math_tags:
                        text = cell.get_text(" ", strip=True)
                        if text:
                            parts.append(text)
                        continue
                    for math_tag in math_tags:
                        math_count += 1
                        tex = _latex_from_math(math_tag)
                        if tex:
                            parts.append(tex)
                if parts:
                    row_texts.append(" ".join(parts))
            if label is None:
                eqno = table.find(class_=lambda c: c and ("ltx_eqn_eqno" in c or "ltx_tag_equation" in c))
                if eqno is not None:
                    label = _clean_label(eqno.get_text(" ", strip=True))
            if not row_texts:
                continue
            tex = " \\\\\n".join(row_texts)
            self.source_counts["math"] += math_count
            self.mapped_counts["math"] += math_count
            self.mapped_counts["equations"] += 1
            self.blocks.append(
                Block(
                    kind="math",
                    id=self._block_id(unit, "eq"),
                    tex=tex,
                    meta={"display": True, "label": label},
                    anchor=self._anchor(unit if unit.name != "tbody" else table),
                )
            )
            appended = True
        return appended

    # ------------------------------------------------------------- tables
    def _convert_table_float(self, figure: Tag) -> None:
        caption_tag = figure.find("figcaption")
        table_tag = figure.find("table")
        if table_tag is None:
            self._append_figure(figure)
            return
        self._append_table(table_tag, caption_tag=caption_tag, wrapper=figure)

    def _append_table(self, table_tag: Tag, caption_tag: Tag | None = None, wrapper: Tag | None = None) -> None:
        if caption_tag is None and wrapper is not None:
            caption_tag = wrapper.find("figcaption")

        caption, label = self._caption_parts(caption_tag)
        rows: list[list[Cell]] = []
        header_rows = 0
        for tr in table_tag.find_all("tr"):
            row: list[Cell] = []
            for cell_tag in tr.find_all(["td", "th"], recursive=False):
                cell_classes = _classes(cell_tag)
                rowspan = _int_attr(cell_tag, "rowspan", 1)
                colspan = _int_attr(cell_tag, "colspan", 1)
                row.append(
                    Cell(
                        inlines=self._inlines(cell_tag),
                        rowspan=rowspan,
                        colspan=colspan,
                        header=(cell_tag.name or "").lower() == "th",
                        align=_alignment(cell_classes),
                    )
                )
            if row:
                rows.append(row)
        thead = table_tag.find("thead")
        if thead is not None:
            header_rows = len(thead.find_all("tr"))
        elif rows and all(cell.header for cell in rows[0]):
            header_rows = 1

        has_spans = any(cell.rowspan != 1 or cell.colspan != 1 for row in rows for cell in row) or header_rows > 1
        source_id = (wrapper or table_tag).get("id")
        table = Table(
            id=str(source_id) if source_id else self._next_id("table"),
            label=label,
            caption=caption,
            header_rows=header_rows,
            rows=rows,
            html=str(table_tag),
            has_spans=has_spans,
            anchor=self._anchor(wrapper or table_tag),
        )
        self.mapped_counts["tables"] += 1
        self.blocks.append(Block(kind="table", id=table.id, table=table, anchor=table.anchor))

    @staticmethod
    def _caption_parts(caption_tag: Tag | None) -> tuple[list[Inline], str | None]:
        if caption_tag is None:
            return [], None
        label: str | None = None
        inlines: list[Inline] = []
        for child in caption_tag.children:
            if isinstance(child, Tag) and "ltx_tag" in _classes(child):
                label = _clean_label(child.get_text(" ", strip=True))
                continue
            if isinstance(child, NavigableString):
                text = _WS_RE.sub(" ", str(child))
                if text:
                    inlines.append(Inline(kind="text", text=text))
            elif isinstance(child, Tag):
                inlines.extend(LatexmlConverter._static_inline(child))
        return _merge_text(inlines), label

    @staticmethod
    def _static_inline(tag: Tag) -> list[Inline]:
        """Inline conversion for captions, where no converter state is required."""
        name = (tag.name or "").lower()
        if name == "math":
            tex = _latex_from_math(tag) or _math_text_fallback(tag)
            return [Inline(kind="math", tex=tex, display=False)]
        if name == "cite":
            keys: list[str] = []
            for anchor in tag.find_all("a"):
                href = str(anchor.get("href") or "")
                if href.startswith("#"):
                    keys.append(href.lstrip("#"))
            label = tag.get_text(" ", strip=True).strip("[]").strip()
            if not keys and label:
                keys = [part.strip() for part in label.split(",") if part.strip()]
            return [Inline(kind="cite", keys=keys, label=label)]
        if name == "a":
            href = tag.get("href")
            children = LatexmlConverter._static_inlines(tag)
            if href:
                return [Inline(kind="link", href=str(href), children=children)]
            return children
        if name in ("b", "strong"):
            return [Inline(kind="strong", children=LatexmlConverter._static_inlines(tag))]
        if name in ("i", "em"):
            return [Inline(kind="emph", children=LatexmlConverter._static_inlines(tag))]
        if name == "code":
            return [Inline(kind="code", text=tag.get_text())]
        if name == "sup":
            return [Inline(kind="sup", children=LatexmlConverter._static_inlines(tag))]
        if name == "sub":
            return [Inline(kind="sub", children=LatexmlConverter._static_inlines(tag))]
        return LatexmlConverter._static_inlines(tag)

    @staticmethod
    def _static_inlines(tag: Tag) -> list[Inline]:
        out: list[Inline] = []
        for child in tag.children:
            if isinstance(child, NavigableString):
                text = _WS_RE.sub(" ", str(child))
                if text:
                    out.append(Inline(kind="text", text=text))
            elif isinstance(child, Tag):
                out.extend(LatexmlConverter._static_inline(child))
        return _merge_text(out)

    # ------------------------------------------------------------ figures
    def _append_figure(self, figure: Tag) -> None:
        classes = _classes(figure)
        caption_tag = figure.find("figcaption")
        caption, label = self._caption_parts(caption_tag) if caption_tag else ([], None)
        images: list[ImageRef] = []
        for img in figure.find_all("img"):
            src = img.get("src") or img.get("data-src")
            srcset = img.get("srcset")
            if srcset:
                candidate = _best_srcset(str(srcset))
                if candidate:
                    src = candidate
            if not src:
                continue
            images.append(ImageRef(src=urljoin(self.base_url, str(src)), alt=str(img.get("alt") or "")))
        for obj in figure.find_all(["object", "embed"]):
            data = obj.get("data") or obj.get("src")
            if data:
                images.append(ImageRef(src=urljoin(self.base_url, str(data)), alt=""))

        source_id = figure.get("id") or self._next_id("figure")
        if "ltx_figure" in classes or images or caption:
            self.mapped_counts["figures"] += 1
            self.blocks.append(
                Block(
                    kind="figure",
                    id=str(source_id),
                    figure=Figure(
                        id=str(source_id),
                        label=label,
                        caption=caption,
                        images=images,
                        anchor=self._anchor(figure),
                    ),
                )
            )
        else:
            self.unmapped.append({"kind": "figure", "id": source_id})

    # ------------------------------------------------------- bibliography
    def _parse_bibliography(self, section: Tag) -> None:
        for li in section.find_all("li"):
            li_classes = _classes(li)
            if "ltx_bibitem" not in li_classes and not (li.get("id") or "").startswith("bib."):
                continue
            entry_id = str(li.get("id") or self._next_id("bib"))
            label_tag = li.find(class_="ltx_tag")
            label = label_tag.get_text(" ", strip=True) if label_tag else ""
            inlines: list[Inline] = []
            raw_parts: list[str] = []
            for block in li.find_all(class_="ltx_bibblock"):
                text = block.get_text(" ", strip=True)
                if text:
                    raw_parts.append(text)
                inlines.extend(self._inlines(block))
            raw_text = " ".join(raw_parts) or li.get_text(" ", strip=True)
            self.bibliography.append(
                BibliographyEntry(id=entry_id, label=label, inlines=inlines, raw_text=raw_text)
            )
            self.mapped_counts["bibitems"] += 1

    # -------------------------------------------------------------- inline
    def _inlines(self, element: Tag | NavigableString) -> list[Inline]:
        out: list[Inline] = []
        children = element.children if isinstance(element, Tag) else [element]
        for child in children:
            if isinstance(child, NavigableString):
                text = _WS_RE.sub(" ", str(child))
                if text:
                    out.append(Inline(kind="text", text=text))
            elif isinstance(child, Tag):
                out.extend(self._inline_from_tag(child))
        return _merge_text(out)

    def _inlines_without(self, element: Tag, skip: list[Tag]) -> list[Inline]:
        out: list[Inline] = []
        for child in element.children:
            if isinstance(child, Tag) and child in skip:
                continue
            if isinstance(child, NavigableString):
                text = _WS_RE.sub(" ", str(child))
                if text:
                    out.append(Inline(kind="text", text=text))
            elif isinstance(child, Tag):
                out.extend(self._inline_from_tag(child))
        return _merge_text(out)

    def _inline_from_tag(self, tag: Tag) -> list[Inline]:
        name = (tag.name or "").lower()
        classes = _classes(tag)

        if name == "math":
            self.source_counts["math"] += 1
            tex = _latex_from_math(tag) or _math_text_fallback(tag)
            if tex:
                self.mapped_counts["math"] += 1
            return [Inline(kind="math", tex=tex, display=tag.get("display") == "block")]

        if name == "img":
            src = tag.get("src") or tag.get("data-src")
            if tag.get("srcset"):
                candidate = _best_srcset(str(tag.get("srcset")))
                if candidate:
                    src = candidate
            if not src:
                return []
            return [Inline(kind="image", src=urljoin(self.base_url, str(src)), alt=str(tag.get("alt") or ""))]

        if "ltx_note" in classes:
            return self._note_inline(tag)

        if name == "cite":
            return self._cite_inline(tag)

        if name == "a":
            href = tag.get("href")
            child_inlines = self._inlines(tag)
            if href:
                resolved = urljoin(self.source_url or self.base_url, str(href))
                return [Inline(kind="link", href=resolved, children=child_inlines)]
            return child_inlines

        if name in ("b", "strong"):
            return [Inline(kind="strong", children=self._inlines(tag))]
        if name in ("i", "em"):
            return [Inline(kind="emph", children=self._inlines(tag))]
        if name == "code":
            return [Inline(kind="code", text=tag.get_text())]
        if name == "sup":
            return [Inline(kind="sup", children=self._inlines(tag))]
        if name == "sub":
            return [Inline(kind="sub", children=self._inlines(tag))]
        if name == "br":
            return [Inline(kind="text", text="\n")]
        if name in ("script", "style"):
            return []

        if "ltx_tag" in classes:
            return self._inlines(tag)

        return self._inlines(tag)

    def _cite_inline(self, tag: Tag) -> list[Inline]:
        keys: list[str] = []
        for anchor in tag.find_all("a"):
            href = str(anchor.get("href") or "")
            if href.startswith("#"):
                keys.append(href.lstrip("#"))
        label = tag.get_text(" ", strip=True)
        label = label.strip("[]").strip()
        if not keys and label:
            keys = [part.strip() for part in label.split(",") if part.strip()]
        return [Inline(kind="cite", keys=keys, label=label)]

    def _note_inline(self, tag: Tag) -> list[Inline]:
        content = tag.find(class_="ltx_note_content")
        if content is None:
            return self._inlines(tag)
        mark = tag.find(class_="ltx_note_mark")
        label = mark.get_text(strip=True) if mark is not None else ""
        if not label:
            self._footnote_seq += 1
            label = str(self._footnote_seq)
        note_id = str(tag.get("id") or f"footnote-{label}")
        self.footnotes.append(
            Block(
                kind="footnote",
                id=note_id,
                inlines=self._inlines(content),
                anchor=self._anchor(tag),
            )
        )
        self.mapped_counts["notes"] += 1
        return [Inline(kind="note_ref", label=label, href=f"#{note_id}")]

    # ------------------------------------------------------------ coverage
    def _count_source_elements(self, main: Tag) -> None:
        soup = main
        body_math = [m for m in soup.find_all("math") if m.find_parent("figure") is None]
        self.source_counts["math"] = max(self.source_counts["math"], len(body_math))
        self.source_counts["figures"] = len(
            [fig for fig in soup.find_all("figure") if "ltx_table" not in _classes(fig)]
        )
        self.source_counts["tables"] = len(soup.find_all("table", class_=lambda c: c and "ltx_tabular" in c))
        self.source_counts["bibitems"] = len(
            [li for li in soup.find_all("li") if "ltx_bibitem" in _classes(li)]
        )
        self.source_counts["notes"] = len(
            [span for span in soup.find_all("span") if "ltx_role_footnote" in _classes(span)]
        )

    def _coverage_report(self) -> dict[str, Any]:
        source = dict(self.source_counts)
        mapped = dict(self.mapped_counts)
        unmapped: list[dict[str, Any]] = list(self.unmapped)
        for kind in ("math", "figures", "tables", "bibitems", "notes"):
            src = source.get(kind, 0)
            got = mapped.get(kind, 0)
            if src > got:
                unmapped.append({"kind": kind, "missing": src - got})
        total_source = sum(source.get(kind, 0) for kind in ("math", "figures", "tables", "bibitems", "notes"))
        total_mapped = sum(
            min(mapped.get(kind, 0), source.get(kind, 0))
            for kind in ("math", "figures", "tables", "bibitems", "notes")
        )
        ratio = 1.0 if total_source == 0 else total_mapped / total_source
        return {
            "source_counts": source,
            "mapped_counts": mapped,
            "unmapped": unmapped,
            "ratio": round(ratio, 4),
            "ok": not unmapped,
        }


def _merge_text(inlines: list[Inline]) -> list[Inline]:
    merged: list[Inline] = []
    for inline in inlines:
        if inline.kind == "text" and merged and merged[-1].kind == "text":
            merged[-1].text += inline.text
        else:
            merged.append(inline)
    return merged


def _has_content(inlines: list[Inline]) -> bool:
    return any(
        (inline.kind == "text" and inline.text.strip())
        or inline.kind not in {"text"}
        for inline in inlines
    )


def _int_attr(tag: Tag, name: str, default: int) -> int:
    value = tag.get(name)
    try:
        return max(1, int(str(value)))
    except (TypeError, ValueError):
        return default


def _alignment(classes: set[str]) -> str | None:
    if "ltx_align_center" in classes or "ltx_align_middle" in classes:
        return "center"
    if "ltx_align_right" in classes:
        return "right"
    if "ltx_align_left" in classes:
        return "left"
    return None


def extract_arxiv_document(
    paper_id: str,
    *,
    key: str,
    source_url: str | None = None,
    html_text: str | None = None,
    timeout_sec: int = 60,
    retries: int = 2,
    backoff: float = 1.5,
    client: HttpClient | None = None,
) -> Document:
    url = source_url or ARXIV_HTML_URL.format(paper_id=paper_id)
    if html_text is None:
        html_text = fetch_arxiv_html(
            paper_id,
            timeout_sec=timeout_sec,
            retries=retries,
            backoff=backoff,
            client=client,
        )
    converter = LatexmlConverter(key=key, source_url=url, paper_id=paper_id, base_url=url)
    return converter.convert(html_text)
