from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from .io_utils import retry_call

USER_AGENT = "paperfetch/2.0 (requests; arxiv-html)"
ARXIV_HTML_URL = "https://arxiv.org/html/{paper_id}"


def fetch_arxiv_html(paper_id: str, timeout_sec: int = 60, retries: int = 2, backoff: float = 1.5) -> str:
    """Download the arXiv HTML version of a paper."""
    url = ARXIV_HTML_URL.format(paper_id=paper_id)
    headers = {"User-Agent": USER_AGENT}

    def _once() -> str:
        resp = requests.get(url, headers=headers, timeout=timeout_sec)
        resp.raise_for_status()
        return resp.text

    return retry_call(
        action_name="arxiv html download",
        func=_once,
        retries=retries,
        backoff_seconds=backoff,
        retriable_exceptions=(requests.RequestException, OSError),
    )


def _latex_from_math(math_tag: Tag) -> str | None:
    """Try to extract LaTeX source from a <math> tag."""
    # 1. alttext attribute
    alt = math_tag.get("alttext")
    if alt:
        return str(alt)
    # 2. <annotation encoding="application/x-tex">
    tex_ann = math_tag.find("annotation", encoding="application/x-tex")
    if tex_ann and tex_ann.string:
        return str(tex_ann.string)
    # 3. data-semantic-latex (some conversions)
    sem = math_tag.get("data-semantic-latex")
    if sem:
        return str(sem)
    return None


def _convert_inline_math(math_tag: Tag) -> str:
    latex = _latex_from_math(math_tag)
    if latex:
        return f"${latex.strip()}$"
    # Fallback: strip tags and wrap
    text = math_tag.get_text(separator=" ").strip()
    if text:
        return f"${text}$"
    return ""


def _convert_display_math(math_tag: Tag) -> str:
    latex = _latex_from_math(math_tag)
    if latex:
        return f"\n\\[\n{latex.strip()}\n\\]\n"
    text = math_tag.get_text(separator=" ").strip()
    if text:
        return f"\n\\[\n{text}\n\\]\n"
    return ""


def _convert_element(elem: Tag | NavigableString, in_math_block: bool = False) -> str:
    """Recursively convert an HTML element to markdown text."""
    if isinstance(elem, NavigableString):
        text = str(elem)
        if in_math_block:
            return text
        # Collapse whitespace outside math
        return text

    if not isinstance(elem, Tag):
        return ""

    tag_name = elem.name.lower() if elem.name else ""

    # Math handling
    if tag_name == "math":
        # Determine if display or inline by parent/context
        parent = elem.parent
        display = elem.get("display") == "block"
        if not display and parent and parent.name in ("p", "span", "a", "cite"):
            return _convert_inline_math(elem)
        return _convert_display_math(elem)

    if tag_name in ("br",):
        return "\n"

    if tag_name == "p":
        inner = "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)
        inner = inner.strip()
        return f"{inner}\n\n" if inner else ""

    if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag_name[1])
        hashes = "#" * level
        inner = "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)
        inner = inner.strip()
        return f"\n{hashes} {inner}\n\n" if inner else ""

    if tag_name in ("b", "strong"):
        inner = "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)
        return f"**{inner.strip()}**" if inner.strip() else ""

    if tag_name in ("i", "em"):
        inner = "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)
        return f"*{inner.strip()}*" if inner.strip() else ""

    if tag_name == "a":
        href = elem.get("href", "")
        inner = "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)
        text = inner.strip()
        if href and text:
            return f"[{text}]({href})"
        return text or href or ""

    if tag_name == "code":
        inner = "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)
        return f"`{inner.strip()}`" if inner.strip() else ""

    if tag_name == "pre":
        inner = "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)
        return f"\n```\n{inner.strip()}\n```\n" if inner.strip() else ""

    if tag_name in ("ul", "ol"):
        items = []
        for li in elem.find_all("li", recursive=False):
            li_text = "".join(_convert_element(child, in_math_block=in_math_block) for child in li.children).strip()
            if li_text:
                prefix = "- " if tag_name == "ul" else "1. "
                items.append(f"{prefix}{li_text}")
        return "\n".join(items) + "\n\n" if items else ""

    if tag_name == "li":
        # Handled by parent ul/ol
        return "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)

    if tag_name == "table":
        return _convert_table(elem)

    if tag_name == "figure":
        return _convert_figure(elem)

    if tag_name in ("div", "span", "section", "article", "header", "footer", "aside"):
        # Transparent containers
        inner = "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)
        return inner

    if tag_name in ("script", "style"):
        return ""

    # Default: just concatenate children
    return "".join(_convert_element(child, in_math_block=in_math_block) for child in elem.children)


def _convert_table(table_tag: Tag) -> str:
    """Convert an HTML table to a markdown table."""
    rows: list[list[str]] = []
    headers: list[str] = []

    thead = table_tag.find("thead")
    if thead:
        for tr in thead.find_all("tr", recursive=False):
            cols = []
            for th in tr.find_all(["th", "td"], recursive=False):
                cols.append("".join(_convert_element(child) for child in th.children).strip().replace("|", "\\|").replace("\n", " "))
            if cols:
                headers = cols

    tbody = table_tag.find("tbody")
    source = tbody if tbody else table_tag
    for tr in source.find_all("tr", recursive=False):
        cols = []
        for td in tr.find_all(["td", "th"], recursive=False):
            cols.append("".join(_convert_element(child) for child in td.children).strip().replace("|", "\\|").replace("\n", " "))
        if cols:
            rows.append(cols)

    if not rows and not headers:
        return ""

    # Normalize column count
    col_count = max(len(headers), max((len(r) for r in rows), default=0))

    def pad(row: list[str]) -> list[str]:
        return row + [""] * (col_count - len(row))

    headers = pad(headers) if headers else [""] * col_count
    rows = [pad(r) for r in rows]

    lines: list[str] = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n\n"


def _convert_figure(figure_tag: Tag) -> str:
    """Convert a figure to markdown with caption."""
    img = figure_tag.find("img")
    src = img.get("src") if img else None
    figcaption = figure_tag.find("figcaption")
    caption = figcaption.get_text(separator=" ").strip() if figcaption else ""

    parts: list[str] = []
    if src:
        # Make relative src absolute if needed
        if src.startswith("/"):
            src = f"https://arxiv.org{src}"
        parts.append(f"![{caption}]({src})")
    if caption:
        parts.append(f"*{caption}*")
    return "\n".join(parts) + "\n\n" if parts else ""


def _is_section_header(tag: Tag) -> bool:
    """Check if a tag looks like a section header in arXiv HTML."""
    classes = tag.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    return "ltx_title_section" in classes or "ltx_title_subsection" in classes or "ltx_title_subsubsection" in classes


def extract_markdown_from_html(html_text: str, paper_id: str | None = None) -> str:
    """Convert arXiv HTML to clean markdown."""
    soup = BeautifulSoup(html_text, "lxml")

    # Remove script/style/nav/header/footer elements
    for elem in soup.find_all(["script", "style", "nav", "header", "footer", "aside"]):
        elem.decompose()

    # Try to find the main content area
    main = soup.find("main") or soup.find("article") or soup.find("div", class_="ltx_page_content")
    if not main:
        main = soup.find("body") or soup

    lines: list[str] = []
    if paper_id:
        lines.append(f"<!-- arXiv:{paper_id} -->")
        lines.append("")

    for child in main.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                lines.append(text)
        elif isinstance(child, Tag):
            if child.name in ("script", "style"):
                continue
            md = _convert_element(child)
            if md.strip():
                lines.append(md)

    raw = "\n".join(lines)
    # Clean up excessive blank lines
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    # Unescape HTML entities that might remain
    raw = html.unescape(raw)
    return raw.strip() + "\n"


def extract_arxiv_markdown(paper_id: str, timeout_sec: int = 60, retries: int = 2, backoff: float = 1.5) -> str:
    """Fetch and convert an arXiv paper's HTML to markdown."""
    html_text = fetch_arxiv_html(paper_id, timeout_sec=timeout_sec, retries=retries, backoff=backoff)
    return extract_markdown_from_html(html_text, paper_id=paper_id)
