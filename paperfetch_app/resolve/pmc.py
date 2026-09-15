from __future__ import annotations

from xml.etree import ElementTree as ET

from ..errors import PaperfetchError
from ..fetch.http import HttpClient
from ..models import PaperIdentity
from .base import STATUS_METADATA_ONLY, STATUS_OK, Resolution, SourceCandidate

OA_API = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
EFETCH_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def _oa_links(pmcid: str, client: HttpClient) -> list[tuple[str, str]]:
    text = client.get_text(OA_API, params={"id": pmcid}, retries=1)
    links: list[tuple[str, str]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return links
    for link in root.iter("link"):
        fmt = str(link.get("format") or "")
        href = str(link.get("href") or "")
        if href:
            links.append((fmt, href))
    return links


def _jats_metadata(pmcid: str, client: HttpClient) -> dict[str, str]:
    number = pmcid.replace("PMC", "")
    text = client.get_text(
        EFETCH_API,
        params={"db": "pmc", "id": number, "rettype": "xml"},
        retries=1,
    )
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    title_tag = root.find(".//article-title")
    title = "".join(title_tag.itertext()).strip() if title_tag is not None else ""
    authors = []
    for contrib in root.findall(".//contrib"):
        surname = contrib.findtext(".//surname")
        given = contrib.findtext(".//given-names")
        if surname:
            authors.append(" ".join(part for part in (given, surname) if part))
    year = ""
    year_tag = root.find(".//pub-date/year")
    if year_tag is not None and year_tag.text:
        year = year_tag.text.strip()
    journal = root.findtext(".//journal-title") or ""
    return {"title": title, "authors": authors, "year": year, "venue": journal}


def resolve_pmc(identity: PaperIdentity, client: HttpClient) -> Resolution:
    pmcid = identity.value
    resolution = Resolution(
        identity=identity,
        landing_url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
    )

    try:
        links = _oa_links(pmcid, client)
        for fmt, href in links:
            normalized = href.replace("ftp://", "https://")
            if fmt == "pdf" or normalized.lower().endswith(".pdf"):
                resolution.candidates.append(
                    SourceCandidate(kind="pdf", url=normalized, extractor="marker", priority=0, label="PMC PDF")
                )
            elif fmt == "tgz":
                resolution.notes.append("PMC full-text package is available as .tar.gz; PDF may not be available")
    except PaperfetchError as exc:
        resolution.notes.append(f"PMC OA lookup failed: {exc}")

    try:
        metadata = _jats_metadata(pmcid, client)
        resolution.title = metadata.get("title") or ""
        resolution.authors = list(metadata.get("authors") or [])
        resolution.venue = metadata.get("venue") or None
        year = metadata.get("year")
        resolution.year = int(year) if str(year).isdigit() else None
        resolution.metadata.update({key: value for key, value in metadata.items() if value})
    except PaperfetchError as exc:
        resolution.notes.append(f"PMC metadata failed: {exc}")

    if not resolution.candidates:
        resolution.status = STATUS_METADATA_ONLY
        resolution.notes.append("no PMC PDF link found")
    else:
        resolution.status = STATUS_OK
    return resolution
