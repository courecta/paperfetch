from __future__ import annotations

from ..fetch.http import HttpClient
from ..models import PaperIdentity
from .base import Resolution, SourceCandidate


def resolve_arxiv(identity: PaperIdentity, client: HttpClient) -> Resolution:
    del client
    paper_id = identity.value
    version = identity.version or ""
    html_url = f"https://arxiv.org/html/{paper_id}{version}"
    pdf_url = f"https://arxiv.org/pdf/{paper_id}{version}.pdf"
    return Resolution(
        identity=identity,
        candidates=[
            SourceCandidate(kind="arxiv_html", url=html_url, extractor="arxiv_html", priority=0, label="arXiv HTML"),
            SourceCandidate(kind="pdf", url=pdf_url, extractor="marker", priority=10, label="arXiv PDF"),
        ],
        arxiv_id=paper_id,
        landing_url=f"https://arxiv.org/abs/{paper_id}{version}",
        notes=[],
    )
