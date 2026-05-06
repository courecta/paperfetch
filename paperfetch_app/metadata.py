from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

from .io_utils import retry_call

USER_AGENT = "paperfetch/2.0 (requests; arxiv-metadata)"
ARXIV_API_URL = "http://export.arxiv.org/api/query"
ARXIV_BIBTEX_URL = "https://arxiv.org/bibtex/{paper_id}"


@dataclass(frozen=True)
class ArxivMetadata:
    paper_id: str
    title: str
    authors: list[str]
    abstract: str
    year: int | None
    categories: list[str]
    primary_category: str | None
    doi: str | None
    published: str | None
    updated: str | None
    bibtex: str | None


def _parse_year_from_date(date_str: str | None) -> int | None:
    if not date_str:
        return None
    m = re.search(r"(\d{4})", date_str)
    return int(m.group(1)) if m else None


def fetch_arxiv_api_metadata(paper_id: str, timeout_sec: int = 30, retries: int = 2, backoff: float = 1.5) -> dict[str, Any]:
    """Fetch raw metadata from arXiv Atom API."""
    params = {"search_query": f"id:{paper_id}", "max_results": "1"}
    headers = {"User-Agent": USER_AGENT}

    def _once() -> dict[str, Any]:
        resp = requests.get(ARXIV_API_URL, params=params, headers=headers, timeout=timeout_sec)
        resp.raise_for_status()
        return {"text": resp.text, "url": resp.url}

    return retry_call(
        action_name="arxiv api metadata",
        func=_once,
        retries=retries,
        backoff_seconds=backoff,
        retriable_exceptions=(requests.RequestException, OSError),
    )


def fetch_arxiv_bibtex(paper_id: str, timeout_sec: int = 30, retries: int = 2, backoff: float = 1.5) -> str | None:
    """Fetch BibTeX entry from arXiv. Returns None if unavailable."""
    url = ARXIV_BIBTEX_URL.format(paper_id=paper_id)
    headers = {"User-Agent": USER_AGENT}

    def _once() -> str:
        resp = requests.get(url, headers=headers, timeout=timeout_sec)
        resp.raise_for_status()
        return resp.text

    try:
        return retry_call(
            action_name="arxiv bibtex",
            func=_once,
            retries=retries,
            backoff_seconds=backoff,
            retriable_exceptions=(requests.RequestException, OSError),
        )
    except Exception:
        return None


def parse_arxiv_api_response(xml_text: str) -> dict[str, Any]:
    """Parse arXiv Atom XML into a plain dict."""
    soup = BeautifulSoup(xml_text, "xml")
    entry = soup.find("entry")
    if not entry:
        raise ValueError("No <entry> found in arXiv API response")

    def _text(tag_name: str) -> str | None:
        tag = entry.find(tag_name)
        return tag.get_text(strip=True) if tag else None

    authors: list[str] = []
    for author in entry.find_all("author"):
        name_tag = author.find("name")
        if name_tag:
            authors.append(name_tag.get_text(strip=True))

    categories: list[str] = []
    primary_category: str | None = None
    for cat in entry.find_all("category"):
        term = cat.get("term")
        if term:
            categories.append(term)
        if cat.get("scheme") == "http://arxiv.org/schemas/atom" and not primary_category:
            primary_category = term

    doi_tag = entry.find("arxiv:doi")
    doi = doi_tag.get_text(strip=True) if doi_tag else None

    published = _text("published")
    updated = _text("updated")
    year = _parse_year_from_date(published)

    return {
        "title": _text("title"),
        "abstract": _text("summary"),
        "authors": authors,
        "categories": categories,
        "primary_category": primary_category,
        "doi": doi,
        "published": published,
        "updated": updated,
        "year": year,
    }


def fetch_arxiv_metadata(paper_id: str, timeout_sec: int = 30, retries: int = 2, backoff: float = 1.5) -> ArxivMetadata:
    """Fetch and parse full metadata for an arXiv paper."""
    raw = fetch_arxiv_api_metadata(paper_id, timeout_sec=timeout_sec, retries=retries, backoff=backoff)
    parsed = parse_arxiv_api_response(raw["text"])

    # Fetch bibtex in parallel or sequentially; keep it simple here
    bibtex = fetch_arxiv_bibtex(paper_id, timeout_sec=timeout_sec, retries=retries, backoff=backoff)

    return ArxivMetadata(
        paper_id=paper_id,
        title=parsed.get("title") or "",
        authors=parsed.get("authors") or [],
        abstract=parsed.get("abstract") or "",
        year=parsed.get("year"),
        categories=parsed.get("categories") or [],
        primary_category=parsed.get("primary_category"),
        doi=parsed.get("doi"),
        published=parsed.get("published"),
        updated=parsed.get("updated"),
        bibtex=bibtex,
    )
