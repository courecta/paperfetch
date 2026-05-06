from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from .io_utils import sha256_text
from .models import PaperIdentity


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "arxiv.org" in host:
        if path.startswith("/abs/"):
            paper_id = path[len("/abs/") :].replace(".pdf", "")
            return f"https://arxiv.org/pdf/{paper_id}.pdf"
        if path.startswith("/pdf/"):
            paper_id = path[len("/pdf/") :].replace(".pdf", "")
            return f"https://arxiv.org/pdf/{paper_id}.pdf"

    if "openreview.net" in host:
        query = parse_qs(parsed.query)
        paper_id = query.get("id", [None])[0]
        if paper_id:
            return f"https://openreview.net/pdf?id={paper_id}"

    return url


def build_identity(url: str) -> PaperIdentity:
    normalized = normalize_url(url.strip())
    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path

    kind = "url"
    value = normalized

    if "arxiv.org" in host and path.startswith("/pdf/"):
        value = path[len("/pdf/") :].replace(".pdf", "")
        kind = "arxiv"
    elif "openreview.net" in host:
        query = parse_qs(parsed.query)
        pid = query.get("id", [None])[0]
        if pid:
            kind = "openreview"
            value = pid
    elif "doi.org" in host and path.strip("/"):
        kind = "doi"
        value = path.strip("/")

    fingerprint = f"{kind}:{value.lower()}"
    key = sha256_text(fingerprint)
    return PaperIdentity(
        kind=kind,
        value=value,
        fingerprint=fingerprint,
        key=key,
        normalized_url=normalized,
    )
