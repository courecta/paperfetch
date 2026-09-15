from __future__ import annotations

from .extract.html_arxiv import (
    ARXIV_HTML_URL,
    LatexmlConverter,
    extract_arxiv_document,
    fetch_arxiv_html,
)
from .extract.render_markdown import render_markdown

__all__ = [
    "ARXIV_HTML_URL",
    "LatexmlConverter",
    "extract_arxiv_markdown",
    "extract_arxiv_document",
    "extract_markdown_from_html",
    "fetch_arxiv_html",
]


def extract_markdown_from_html(html_text: str, paper_id: str | None = None) -> str:
    """Convert legacy HTML into markdown using the IR converter."""
    converter = LatexmlConverter(
        key=f"paper:{paper_id or 'unknown'}",
        source_url=ARXIV_HTML_URL.format(paper_id=paper_id) if paper_id else "",
        base_url=ARXIV_HTML_URL.format(paper_id=paper_id) if paper_id else "",
    )
    document = converter.convert(html_text)
    return render_markdown(document, include_frontmatter=False)


def extract_arxiv_markdown(paper_id: str, timeout_sec: int = 60, retries: int = 2, backoff: float = 1.5) -> str:
    document = extract_arxiv_document(
        paper_id,
        key=f"paper:{paper_id}",
        source_url=ARXIV_HTML_URL.format(paper_id=paper_id),
        timeout_sec=timeout_sec,
        retries=retries,
        backoff=backoff,
    )
    return render_markdown(document, include_frontmatter=False)
