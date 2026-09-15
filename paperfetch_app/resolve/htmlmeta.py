from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup


def parse_citation_meta(html_text: str, base_url: str) -> dict[str, Any]:
    """Extract Highwire citation tags, OpenGraph, and JSON-LD scholarly metadata."""
    soup = BeautifulSoup(html_text, "lxml")
    meta: dict[str, Any] = {}

    def _content(name: str) -> str | None:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
        return None

    title = _content("citation_title") or _content("og:title")
    if title:
        meta["title"] = title

    authors = [
        str(tag.get("content")).strip()
        for tag in soup.find_all("meta", attrs={"name": "citation_author"})
        if tag.get("content")
    ]
    if authors:
        meta["authors"] = authors

    doi = _content("citation_doi")
    if doi:
        meta["doi"] = doi.strip().lower()

    date = _content("citation_publication_date") or _content("citation_date") or _content("og:updated_time")
    if date:
        match = re.search(r"(\d{4})", date)
        if match:
            meta["year"] = int(match.group(1))

    venue = _content("citation_journal_title") or _content("citation_conference_title")
    if venue:
        meta["venue"] = venue

    pdf_url = _content("citation_pdf_url")
    if pdf_url:
        meta["pdf_url"] = urljoin(base_url, pdf_url)

    abstract = _content("citation_abstract") or _content("description")
    if abstract:
        meta["abstract"] = abstract

    if "pdf_url" not in meta:
        link = soup.find("link", attrs={"type": "application/pdf"})
        if link and link.get("href"):
            meta["pdf_url"] = urljoin(base_url, str(link.get("href")))

    if "pdf_url" not in meta:
        anchor = soup.find("a", href=re.compile(r"\.pdf($|\?)", re.IGNORECASE))
        if anchor and anchor.get("href"):
            meta["pdf_url"] = urljoin(base_url, str(anchor.get("href")))

    if "pdf_url" not in meta:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(script.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            for item in payload if isinstance(payload, list) else [payload]:
                if not isinstance(item, dict):
                    continue
                candidate = item.get("encoding") or item.get("associatedMedia") or {}
                if isinstance(candidate, dict):
                    content_url = candidate.get("contentUrl")
                    if content_url:
                        meta["pdf_url"] = urljoin(base_url, str(content_url))
                        break
                if item.get("pdfUrl"):
                    meta["pdf_url"] = urljoin(base_url, str(item["pdfUrl"]))
                    break
            if "pdf_url" in meta:
                break

    return meta
