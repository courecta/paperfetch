from __future__ import annotations

from typing import Any

from ..errors import PaperfetchError
from ..fetch.http import HttpClient
from ..models import PaperIdentity
from .base import STATUS_OK, Resolution, SourceCandidate

API_V2 = "https://api2.openreview.net/notes"
API_V1 = "https://api.openreview.net/notes"


def _metadata_from_notes(paper_id: str, client: HttpClient) -> dict[str, Any]:
    for api in (API_V2, API_V1):
        try:
            data = client.get_json(api, params={"id": paper_id}, retries=1)
        except PaperfetchError:
            continue
        notes = data.get("notes", []) if isinstance(data, dict) else []
        if not notes:
            continue
        content = notes[0].get("content", {}) or {}

        def _value(key: str, content: dict[str, Any] = content) -> Any:
            raw = content.get(key)
            if isinstance(raw, dict):
                raw = raw.get("value")
            return raw

        authors_raw = _value("authors")
        if isinstance(authors_raw, list):
            authors = [str(author) for author in authors_raw if author]
        elif isinstance(authors_raw, str):
            authors = [part.strip() for part in authors_raw.split(",") if part.strip()]
        else:
            authors = []

        return {
            "title": str(_value("title") or ""),
            "authors": authors,
            "abstract": str(_value("abstract") or ""),
            "venue": str(_value("venue") or ""),
        }
    return {}


def resolve_openreview(identity: PaperIdentity, client: HttpClient) -> Resolution:
    paper_id = identity.value
    resolution = Resolution(
        identity=identity,
        landing_url=f"https://openreview.net/forum?id={paper_id}",
    )
    resolution.candidates.append(
        SourceCandidate(
            kind="pdf",
            url=f"https://openreview.net/pdf?id={paper_id}",
            extractor="marker",
            priority=0,
            label="OpenReview PDF",
        )
    )
    try:
        metadata = _metadata_from_notes(paper_id, client)
        if metadata.get("title"):
            resolution.title = metadata["title"]
            resolution.metadata["title"] = metadata["title"]
        if metadata.get("authors"):
            resolution.metadata["authors"] = metadata["authors"]
            resolution.authors = list(metadata["authors"])
        if metadata.get("abstract"):
            resolution.abstract = metadata["abstract"]
            resolution.metadata["abstract"] = metadata["abstract"]
        if metadata.get("venue"):
            resolution.venue = metadata["venue"]
    except Exception as exc:  # noqa: BLE001
        resolution.notes.append(f"openreview metadata failed: {exc}")
    resolution.status = STATUS_OK
    return resolution
