from __future__ import annotations

from urllib.parse import urlparse

from ..errors import PaperfetchError
from ..fetch.http import HttpClient
from ..models import PaperIdentity
from .base import STATUS_METADATA_ONLY, STATUS_OK, Resolution, SourceCandidate
from .htmlmeta import parse_citation_meta


def _pdf_candidates_from_value(kind: str, host: str, value: str) -> tuple[str, list[str]]:
    clean = value.strip("/")
    if kind == "acl":
        return f"https://aclanthology.org/{clean}/", [f"https://aclanthology.org/{clean}.pdf"]
    if kind == "pmlr":
        base = clean.rsplit("/", 1)[-1]
        return f"https://proceedings.mlr.press/{clean}.html", [
            f"https://proceedings.mlr.press/{clean}/{base}.pdf",
            f"https://proceedings.mlr.press/{clean}.pdf",
        ]
    if kind == "cvf":
        if "/html/" in clean:
            pdf = clean.replace("/html/", "/papers/") + ".pdf"
            landing = f"https://{host}/{clean}.html"
            return landing, [f"https://{host}/{pdf}"]
        return f"https://{host}/{clean}.html", [f"https://{host}/{clean}.pdf"]
    if kind == "jmlr":
        return f"https://jmlr.org/papers/{clean}.html", [f"https://jmlr.org/papers/{clean}.pdf"]
    if kind == "neurips":
        return f"https://proceedings.neurips.cc/{clean}.html", [f"https://proceedings.neurips.cc/{clean}.pdf"]
    return f"https://{host}/{clean}", [f"https://{host}/{clean}.pdf"]


def resolve_proceedings(identity: PaperIdentity, client: HttpClient) -> Resolution:
    host = urlparse(identity.normalized_url).hostname or ""
    landing, constructed_pdfs = _pdf_candidates_from_value(identity.kind, host, identity.value)
    resolution = Resolution(identity=identity, landing_url=landing, metadata={})

    pdf_urls: list[str] = []
    try:
        html_text = client.get_text(landing, retries=1, backoff=1.0)
        meta = parse_citation_meta(html_text, landing)
        resolution.metadata.update(meta)
        resolution.title = str(meta.get("title") or "")
        resolution.authors = list(meta.get("authors") or [])
        resolution.venue = meta.get("venue")
        resolution.doi = meta.get("doi")
        year = meta.get("year")
        resolution.year = int(year) if year else None
        if meta.get("pdf_url"):
            pdf_urls.append(str(meta["pdf_url"]))
    except PaperfetchError as exc:
        resolution.notes.append(f"landing page metadata failed: {exc}")

    pdf_urls.extend(constructed_pdfs)
    seen: set[str] = set()
    for url in pdf_urls:
        if url in seen:
            continue
        seen.add(url)
        resolution.candidates.append(
            SourceCandidate(kind="pdf", url=url, extractor="marker", priority=len(resolution.candidates), label="Proceedings PDF")
        )

    if not resolution.candidates:
        resolution.status = STATUS_METADATA_ONLY
    else:
        resolution.status = STATUS_OK
    return resolution
