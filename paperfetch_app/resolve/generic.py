from __future__ import annotations

from urllib.parse import urlparse

from ..errors import PaperfetchError
from ..fetch.http import HttpClient
from ..models import PaperIdentity
from .base import STATUS_METADATA_ONLY, STATUS_OK, Resolution, SourceCandidate
from .htmlmeta import parse_citation_meta


def _looks_like_pdf(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or path.endswith("/pdf")


def resolve_generic(identity: PaperIdentity, client: HttpClient) -> Resolution:
    url = identity.normalized_url
    resolution = Resolution(identity=identity, landing_url=url)
    if _looks_like_pdf(url):
        resolution.candidates.append(SourceCandidate(kind="pdf", url=url, extractor="marker", priority=0, label="PDF"))
        return resolution

    try:
        html_text = client.get_text(url, retries=2, backoff=1.0)
    except PaperfetchError as exc:
        resolution.status = STATUS_METADATA_ONLY
        resolution.notes.append(f"landing page unavailable: {exc}")
        return resolution

    meta = parse_citation_meta(html_text, url)
    resolution.metadata = meta
    resolution.title = str(meta.get("title") or "")
    resolution.authors = list(meta.get("authors") or [])
    resolution.abstract = str(meta.get("abstract") or "")
    resolution.venue = meta.get("venue")
    resolution.doi = meta.get("doi")
    year = meta.get("year")
    resolution.year = int(year) if year else None

    pdf_url = meta.get("pdf_url")
    if pdf_url:
        resolution.candidates.append(
            SourceCandidate(kind="pdf", url=str(pdf_url), extractor="marker", priority=0, label="PDF")
        )
        resolution.status = STATUS_OK
        return resolution

    resolution.status = STATUS_METADATA_ONLY
    resolution.notes.append("no PDF link found on landing page; provide a direct PDF URL")
    return resolution
