from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .fetch.http import HttpClient
from .io_utils import safe_slug

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,authors,year,abstract,openAccessPdf,venue,externalIds,isOpenAccess,publicationTypes,citationCount"


@dataclass(frozen=True)
class DiscoveredPaper:
    paper_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    venue: str | None
    url: str | None
    arxiv_id: str | None
    doi: str | None
    is_open_access: bool
    citation_count: int | None


def _to_discovered(raw: dict[str, Any]) -> DiscoveredPaper | None:
    title = raw.get("title")
    if not title:
        return None

    authors = [a.get("name", "") for a in raw.get("authors", []) if a.get("name")]
    year = raw.get("year")
    abstract = raw.get("abstract")
    venue = raw.get("venue")
    citation_count = raw.get("citationCount")

    open_access_pdf = raw.get("openAccessPdf")
    pdf_url = open_access_pdf.get("url") if isinstance(open_access_pdf, dict) else None

    external_ids = raw.get("externalIds", {}) or {}
    arxiv_id = external_ids.get("ArXiv") or external_ids.get("arXiv")
    doi = external_ids.get("DOI")

    if arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"
    elif doi:
        url = f"https://doi.org/{doi}"
    elif pdf_url:
        url = pdf_url
    else:
        # paperId is present-but-null on bibliography-extracted records, so a
        # plain .get default would build ".../paper/None".
        paper_id = raw.get("paperId") or ""
        url = f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None

    return DiscoveredPaper(
        paper_id=raw.get("paperId") or "",
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        venue=venue,
        url=url,
        arxiv_id=arxiv_id,
        doi=doi,
        is_open_access=bool(raw.get("isOpenAccess")),
        citation_count=citation_count,
    )


def search_semantic_scholar(
    query: str,
    limit: int = 20,
    year_start: int | None = None,
    year_end: int | None = None,
    open_access_only: bool = False,
    venue: str | None = None,
    timeout_sec: int = 30,
    retries: int = 2,
    backoff: float = 1.5,
    client: HttpClient | None = None,
) -> list[DiscoveredPaper]:
    """Search Semantic Scholar and return discovered papers."""
    params: dict[str, str | int] = {
        "query": query,
        "fields": S2_FIELDS,
        "limit": limit,
    }
    if year_start is not None or year_end is not None:
        start = year_start if year_start is not None else ""
        end = year_end if year_end is not None else ""
        params["publicationDateOrYear"] = f"{start}:{end}"
    if open_access_only:
        params["openAccessPdf"] = "true"
    if venue:
        params["venue"] = venue

    owns = client is None
    client = client or HttpClient(timeout=timeout_sec, retries=retries, backoff=backoff)
    try:
        data = client.get_json(
            S2_SEARCH_URL,
            params=params,
            timeout=timeout_sec,
            retries=retries,
            backoff=backoff,
        )
    finally:
        if owns:
            client.close()

    results: list[DiscoveredPaper] = []
    for raw in data.get("data", []):
        paper = _to_discovered(raw)
        if paper:
            results.append(paper)
    return results


def lookup_paper(
    s2_id: str,
    *,
    client: HttpClient | None = None,
    timeout_sec: int = 30,
    retries: int = 1,
    backoff: float = 1.5,
) -> DiscoveredPaper | None:
    """Look up one paper by Semantic Scholar id (e.g. "arXiv:2504.05662")."""
    owns = client is None
    client = client or HttpClient(timeout=timeout_sec, retries=retries, backoff=backoff)
    try:
        data = client.get_json(
            f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}",
            params={"fields": S2_FIELDS},
            timeout=timeout_sec,
            retries=retries,
            backoff=backoff,
        )
    finally:
        if owns:
            client.close()
    return _to_discovered(data)


def format_discovered(papers: list[DiscoveredPaper], fmt: str) -> str:
    """Format discovered papers for output."""
    if fmt == "urls":
        lines = [p.url for p in papers if p.url]
        return "\n".join(lines) + "\n" if lines else ""

    if fmt == "tsv":
        lines = ["slug\ttitle\turl\tyear\tvenue"]
        for p in papers:
            slug = safe_slug(p.title)
            lines.append(f"{slug}\t{p.title}\t{p.url or ''}\t{p.year or ''}\t{p.venue or ''}")
        return "\n".join(lines) + "\n"

    if fmt in ("json", "manifest"):
        payload = []
        for p in papers:
            entry: dict[str, Any] = {
                "slug": safe_slug(p.title),
                "title": p.title,
                "url": p.url,
            }
            if p.year:
                entry["year"] = p.year
            if p.venue:
                entry["venue"] = p.venue
            if p.abstract:
                entry["abstract"] = p.abstract
            if p.arxiv_id:
                entry["arxiv_id"] = p.arxiv_id
            if p.doi:
                entry["doi"] = p.doi
            payload.append(entry)
        return json.dumps(payload, indent=2) + "\n"

    raise ValueError(f"Unsupported format: {fmt}")
