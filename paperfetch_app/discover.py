from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .io_utils import retry_call, safe_slug

USER_AGENT = "paperfetch/2.0 (requests; semantic-scholar)"
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
    url = open_access_pdf.get("url") if isinstance(open_access_pdf, dict) else None

    external_ids = raw.get("externalIds", {}) or {}
    arxiv_id = external_ids.get("ArXiv") or external_ids.get("arXiv")
    doi = external_ids.get("DOI")

    # Prefer arXiv abstract URL if available
    if arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"
    elif doi:
        url = f"https://doi.org/{doi}"
    elif not url:
        # Fallback to Semantic Scholar page
        url = f"https://www.semanticscholar.org/paper/{raw.get('paperId', '')}"

    is_open_access = bool(raw.get("isOpenAccess"))

    return DiscoveredPaper(
        paper_id=raw.get("paperId", ""),
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        venue=venue,
        url=url,
        arxiv_id=arxiv_id,
        doi=doi,
        is_open_access=is_open_access,
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
) -> list[DiscoveredPaper]:
    """Search Semantic Scholar and return discovered papers."""
    params: dict[str, str | int] = {
        "query": query,
        "fields": S2_FIELDS,
        "limit": limit,
    }

    # Build year filter string if provided
    if year_start is not None or year_end is not None:
        start = year_start if year_start is not None else ""
        end = year_end if year_end is not None else ""
        params["publicationDateOrYear"] = f"{start}:{end}"

    if open_access_only:
        params["openAccessPdf"] = "true"

    if venue:
        params["venue"] = venue

    headers = {"User-Agent": USER_AGENT}

    def _once() -> dict[str, Any]:
        resp = requests.get(S2_SEARCH_URL, params=params, headers=headers, timeout=timeout_sec)
        resp.raise_for_status()
        return resp.json()

    data = retry_call(
        action_name="semantic scholar search",
        func=_once,
        retries=retries,
        backoff_seconds=backoff,
        retriable_exceptions=(requests.RequestException, OSError),
    )

    results: list[DiscoveredPaper] = []
    for raw in data.get("data", []):
        paper = _to_discovered(raw)
        if paper:
            results.append(paper)
    return results


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
            entry = {
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
            payload.append(entry)
        return json.dumps(payload, indent=2) + "\n"

    raise ValueError(f"Unsupported format: {fmt}")
