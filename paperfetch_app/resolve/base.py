from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..fetch.http import HttpClient
from ..models import PaperIdentity

STATUS_OK = "ok"
STATUS_METADATA_ONLY = "metadata_only"
STATUS_UNAVAILABLE = "unavailable"


@dataclass
class SourceCandidate:
    kind: str  # arxiv_html | pdf | html | jats
    url: str
    extractor: str  # arxiv_html | marker | html_generic
    priority: int = 0
    label: str = ""


@dataclass
class Resolution:
    identity: PaperIdentity
    candidates: list[SourceCandidate] = field(default_factory=list)
    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    license: str | None = None
    landing_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    status: str = STATUS_OK

    def ordered_candidates(self) -> list[SourceCandidate]:
        return sorted(self.candidates, key=lambda candidate: candidate.priority)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": {
                "kind": self.identity.kind,
                "value": self.identity.value,
                "key": self.identity.key,
                "normalized_url": self.identity.normalized_url,
            },
            "status": self.status,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "license": self.license,
            "notes": self.notes,
            "candidates": [
                {"kind": c.kind, "url": c.url, "extractor": c.extractor, "priority": c.priority, "label": c.label}
                for c in self.ordered_candidates()
            ],
        }


def resolve(identity: PaperIdentity, client: HttpClient) -> Resolution:
    """Resolve an identity into concrete fetchable candidates."""
    from .arxiv import resolve_arxiv
    from .generic import resolve_generic

    if identity.kind == "arxiv":
        return resolve_arxiv(identity, client)
    if identity.kind in {"doi", "pmcid", "openreview", "biorxiv", "medrxiv", "acl", "pmlr", "cvf", "jmlr", "neurips"}:
        from .registry import resolve_extended

        resolution = resolve_extended(identity, client)
        if resolution is not None:
            return resolution
    return resolve_generic(identity, client)
