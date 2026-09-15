from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from .fetch.http import HttpClient

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_BIBTEX_URL = "https://arxiv.org/bibtex/{paper_id}"
CROSSREF_API_URL = "https://api.crossref.org/works/{doi}"
OPENALEX_API_URL = "https://api.openalex.org/works/doi:{doi}"
UNPAYWALL_API_URL = "https://api.unpaywall.org/v2/{doi}"


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "year": self.year,
            "categories": self.categories,
            "primary_category": self.primary_category,
            "doi": self.doi,
            "published": self.published,
            "updated": self.updated,
            "bibtex": self.bibtex,
            "arxiv_id": self.paper_id,
        }


def _parse_year_from_date(date_str: str | None) -> int | None:
    if not date_str:
        return None
    match = re.search(r"(\d{4})", date_str)
    return int(match.group(1)) if match else None


def _client(client: HttpClient | None, **kwargs: Any) -> tuple[HttpClient, bool]:
    if client is not None:
        return client, False
    return HttpClient(**kwargs), True


def fetch_arxiv_api_metadata(
    paper_id: str,
    timeout_sec: int = 30,
    retries: int = 2,
    backoff: float = 1.5,
    client: HttpClient | None = None,
) -> str:
    owns = client is None
    client = client or HttpClient(timeout=timeout_sec, retries=retries, backoff=backoff)
    try:
        return client.get_text(
            ARXIV_API_URL,
            params={"search_query": f"id:{paper_id}", "max_results": "1"},
            timeout=timeout_sec,
            retries=retries,
            backoff=backoff,
        )
    finally:
        if owns:
            client.close()


def fetch_arxiv_bibtex(
    paper_id: str,
    timeout_sec: int = 30,
    retries: int = 2,
    backoff: float = 1.5,
    client: HttpClient | None = None,
) -> str | None:
    url = ARXIV_BIBTEX_URL.format(paper_id=paper_id)
    owns = client is None
    client = client or HttpClient(timeout=timeout_sec, retries=retries, backoff=backoff)
    try:
        return client.get_text(url, timeout=timeout_sec, retries=retries, backoff=backoff)
    except Exception:
        return None
    finally:
        if owns:
            client.close()


def parse_arxiv_api_response(xml_text: str) -> dict[str, Any]:
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


def fetch_arxiv_metadata(
    paper_id: str,
    timeout_sec: int = 30,
    retries: int = 2,
    backoff: float = 1.5,
    client: HttpClient | None = None,
) -> ArxivMetadata:
    owns = client is None
    client = client or HttpClient(timeout=timeout_sec, retries=retries, backoff=backoff)
    try:
        xml_text = fetch_arxiv_api_metadata(
            paper_id, timeout_sec=timeout_sec, retries=retries, backoff=backoff, client=client
        )
        parsed = parse_arxiv_api_response(xml_text)
        bibtex = fetch_arxiv_bibtex(
            paper_id, timeout_sec=timeout_sec, retries=retries, backoff=backoff, client=client
        )
    finally:
        if owns:
            client.close()

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
