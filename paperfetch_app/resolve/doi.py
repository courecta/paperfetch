from __future__ import annotations

from typing import Any

from ..config import get_mailto
from ..errors import PaperfetchError
from ..fetch.http import HttpClient
from ..models import PaperIdentity
from .base import STATUS_METADATA_ONLY, STATUS_OK, Resolution, SourceCandidate

CROSSREF_API_URL = "https://api.crossref.org/works/{doi}"
OPENALEX_API_URL = "https://api.openalex.org/works/doi:{doi}"
UNPAYWALL_API_URL = "https://api.unpaywall.org/v2/{doi}"
S2_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
S2_FIELDS = "title,authors,year,venue,openAccessPdf,externalIds,isOpenAccess,abstract"


def _crossref_metadata(doi: str, client: HttpClient) -> dict[str, Any]:
    data = client.get_json(CROSSREF_API_URL.format(doi=doi), retries=1)
    message = data.get("message", {}) if isinstance(data, dict) else {}
    title = (message.get("title") or [""])[0]
    authors = []
    for author in message.get("author", []) or []:
        name = " ".join(part for part in (author.get("given"), author.get("family")) if part)
        if name:
            authors.append(name)
    year = None
    for key in ("published-print", "published-online", "issued"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    return {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": (message.get("container-title") or [""])[0],
        "doi": doi,
        "license": ((message.get("license") or [{}])[0].get("URL")),
    }


def _openalex_oa(doi: str, client: HttpClient) -> tuple[list[str], dict[str, Any]]:
    params: dict[str, Any] = {}
    mailto = get_mailto()
    if mailto:
        params["mailto"] = mailto
    data = client.get_json(OPENALEX_API_URL.format(doi=doi), params=params or None, retries=1)
    if not isinstance(data, dict):
        return [], {}
    pdfs: list[str] = []
    best = data.get("best_oa_location") or {}
    for key in ("pdf_url", "landing_page_url"):
        url = best.get(key)
        if key == "pdf_url" and url:
            pdfs.append(str(url))
    for location in data.get("locations", []) or []:
        url = location.get("pdf_url")
        if url:
            pdfs.append(str(url))
    metadata = {
        "title": data.get("title"),
        "year": data.get("publication_year"),
        "venue": ((data.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "license": ((data.get("best_oa_location") or {}).get("license")),
    }
    return pdfs, {key: value for key, value in metadata.items() if value}


def _unpaywall_pdf(doi: str, client: HttpClient) -> str | None:
    mailto = get_mailto()
    if not mailto:
        return None
    data = client.get_json(UNPAYWALL_API_URL.format(doi=doi), params={"email": mailto}, retries=1)
    if not isinstance(data, dict):
        return None
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or best.get("url")


def _s2_pdf(doi: str, client: HttpClient) -> tuple[str | None, dict[str, Any]]:
    data = client.get_json(S2_PAPER_URL.format(doi=doi), params={"fields": S2_FIELDS}, retries=1)
    if not isinstance(data, dict):
        return None, {}
    pdf = (data.get("openAccessPdf") or {}).get("url")
    metadata = {
        "title": data.get("title"),
        "year": data.get("year"),
        "venue": data.get("venue"),
        "abstract": data.get("abstract"),
    }
    return pdf, {key: value for key, value in metadata.items() if value}


def resolve_doi_like(identity: PaperIdentity, client: HttpClient) -> Resolution:
    doi = identity.value
    resolution = Resolution(identity=identity, landing_url=f"https://doi.org/{doi}", doi=doi)

    if identity.kind in {"biorxiv", "medrxiv"}:
        resolution.metadata["server"] = identity.kind
        resolution.candidates.append(
            SourceCandidate(kind="pdf", url=identity.normalized_url, extractor="marker", priority=0, label=f"{identity.kind} PDF")
        )
        resolution.landing_url = (
            f"https://www.{identity.kind}.org/content/{doi}"
        )
        return resolution

    try:
        metadata = _crossref_metadata(doi, client)
        resolution.metadata.update(metadata)
        resolution.title = str(metadata.get("title") or "")
        resolution.authors = list(metadata.get("authors") or [])
        resolution.year = metadata.get("year")
        resolution.venue = metadata.get("venue")
        resolution.license = metadata.get("license")
    except PaperfetchError as exc:
        resolution.notes.append(f"crossref metadata failed: {exc}")

    pdf_urls: list[str] = []
    try:
        pdfs, oa_meta = _openalex_oa(doi, client)
        pdf_urls.extend(pdfs)
        for key, value in oa_meta.items():
            resolution.metadata.setdefault(key, value)
        resolution.license = resolution.license or oa_meta.get("license")
    except PaperfetchError as exc:
        resolution.notes.append(f"openalex lookup failed: {exc}")

    try:
        unpaywall = _unpaywall_pdf(doi, client)
        if unpaywall:
            pdf_urls.append(unpaywall)
    except PaperfetchError as exc:
        resolution.notes.append(f"unpaywall lookup failed: {exc}")

    try:
        s2_pdf, s2_meta = _s2_pdf(doi, client)
        if s2_pdf:
            pdf_urls.append(s2_pdf)
        for key, value in s2_meta.items():
            resolution.metadata.setdefault(key, value)
    except PaperfetchError as exc:
        resolution.notes.append(f"semantic scholar lookup failed: {exc}")

    seen: set[str] = set()
    for url in pdf_urls:
        if url in seen:
            continue
        seen.add(url)
        resolution.candidates.append(
            SourceCandidate(kind="pdf", url=url, extractor="marker", priority=len(resolution.candidates), label="Open access PDF")
        )

    if not resolution.candidates:
        resolution.status = STATUS_METADATA_ONLY
        resolution.notes.append("no open-access PDF found; metadata-only record")
    else:
        resolution.status = STATUS_OK
    return resolution
