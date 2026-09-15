from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from .io_utils import sha256_text
from .models import PaperIdentity

ARXIV_ID_RE = re.compile(
    r"^(?P<id>(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.\-]+(?:\.[A-Z]{2})?/\d{7}))(?P<version>v\d+)?$"
)
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
PMCID_RE = re.compile(r"^(?:PMC)?(?P<number>\d{6,9})$", re.IGNORECASE)

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "ref",
    "referrer",
    "source",
    "spm",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "sk",
    "si",
}


def _host_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def _strip_tracking(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in TRACKING_QUERY_KEYS or any(lowered.startswith(p) for p in TRACKING_QUERY_PREFIXES):
            continue
        kept.append((key, value))
    query = urlencode(kept, doseq=True)
    return urlunparse(parsed._replace(query=query, fragment=""))


def normalize_doi(value: str) -> str:
    doi = value.strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    return doi.strip().strip("/").lower()


def normalize_arxiv_id(value: str) -> tuple[str, str | None]:
    """Return (id_without_version, version or None)."""
    candidate = value.strip()
    candidate = re.sub(r"^arxiv:\s*", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\.pdf$", "", candidate, flags=re.IGNORECASE)
    match = ARXIV_ID_RE.match(candidate)
    if not match:
        return candidate, None
    return match.group("id"), match.group("version")


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return url.strip()
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    return _strip_tracking(urlunparse((scheme, netloc, path, "", parsed.query, "")))


def normalize_url(url: str) -> str:
    """Return a canonical download URL for known sources, else a cleaned URL."""
    identity = build_identity(url)
    return identity.normalized_url


def build_identity(url: str) -> PaperIdentity:
    raw = url.strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    path = parsed.path
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if DOI_RE.match(raw) and not parsed.scheme:
        return _doi_identity(normalize_doi(raw))

    if _host_matches(host, "arxiv.org"):
        return _arxiv_identity(path, raw)

    if _host_matches(host, "doi.org") and path.strip("/"):
        return _doi_identity(normalize_doi(path))

    if _host_matches(host, "openreview.net"):
        paper_id = query.get("id")
        if paper_id:
            fingerprint = f"openreview:{paper_id.lower()}"
            return PaperIdentity(
                kind="openreview",
                value=paper_id,
                fingerprint=fingerprint,
                key=sha256_text(fingerprint),
                normalized_url=f"https://openreview.net/pdf?id={quote(paper_id)}",
            )
        paper_id = query.get("noteId")
        if paper_id:
            fingerprint = f"openreview:{paper_id.lower()}"
            return PaperIdentity(
                kind="openreview",
                value=paper_id,
                fingerprint=fingerprint,
                key=sha256_text(fingerprint),
                normalized_url=f"https://openreview.net/pdf?id={quote(paper_id)}",
            )

    if _host_matches(host, "ncbi.nlm.nih.gov") and "pmc" in path.lower():
        match = re.search(r"/(PMC\d+)", path, re.IGNORECASE)
        if match:
            return _pmc_identity(match.group(1))

    if _host_matches(host, "biorxiv.org") or _host_matches(host, "medrxiv.org"):
        return _biorxiv_identity(host, path)

    if _host_matches(host, "aclanthology.org"):
        return _proceedings_identity("acl", host, path, suffix=".pdf")

    if _host_matches(host, "proceedings.mlr.press"):
        return _proceedings_identity("pmlr", host, path)

    if _host_matches(host, "openaccess.thecvf.com"):
        return _proceedings_identity("cvf", host, path)

    if _host_matches(host, "jmlr.org"):
        return _proceedings_identity("jmlr", host, path, suffix=".pdf")

    if _host_matches(host, "proceedings.neurips.cc"):
        return _proceedings_identity("neurips", host, path, suffix=".pdf")

    if _host_matches(host, "papers.nips.cc"):
        return _proceedings_identity("neurips", host, path, suffix=".pdf")

    canonical = canonicalize_url(raw)
    fingerprint = f"url:{canonical.lower()}"
    return PaperIdentity(
        kind="url",
        value=canonical,
        fingerprint=fingerprint,
        key=sha256_text(fingerprint),
        normalized_url=canonical,
    )


def _arxiv_identity(path: str, raw: str) -> PaperIdentity:
    candidate = ""
    for prefix in ("/abs/", "/pdf/", "/html/"):
        if path.startswith(prefix):
            candidate = path[len(prefix) :]
            break
    if not candidate:
        match = re.search(r"(?:abs|pdf|html)/(.+)$", path)
        candidate = match.group(1) if match else path.lstrip("/")
    paper_id, version = normalize_arxiv_id(candidate)
    if not paper_id:
        canonical = canonicalize_url(raw)
        fingerprint = f"url:{canonical.lower()}"
        return PaperIdentity(
            kind="url",
            value=canonical,
            fingerprint=fingerprint,
            key=sha256_text(fingerprint),
            normalized_url=canonical,
        )
    version_suffix = version or ""
    fingerprint = f"arxiv:{paper_id.lower()}"
    return PaperIdentity(
        kind="arxiv",
        value=paper_id,
        fingerprint=fingerprint,
        key=sha256_text(fingerprint),
        normalized_url=f"https://arxiv.org/pdf/{paper_id}{version_suffix}.pdf",
        version=version,
    )


def _doi_identity(doi: str) -> PaperIdentity:
    fingerprint = f"doi:{doi}"
    return PaperIdentity(
        kind="doi",
        value=doi,
        fingerprint=fingerprint,
        key=sha256_text(fingerprint),
        normalized_url=f"https://doi.org/{doi}",
    )


def _pmc_identity(value: str) -> PaperIdentity:
    match = PMCID_RE.match(value.strip())
    number = match.group("number") if match else value.strip()
    pmcid = f"PMC{number}"
    fingerprint = f"pmcid:{pmcid.lower()}"
    return PaperIdentity(
        kind="pmcid",
        value=pmcid,
        fingerprint=fingerprint,
        key=sha256_text(fingerprint),
        normalized_url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
    )


def _biorxiv_identity(host: str, path: str) -> PaperIdentity:
    match = re.search(r"/content/(10\.\d{4,9}/[^/?#]+?)(v\d+)?(?:\.full)?(?:\.pdf)?$", path)
    if match:
        value = match.group(1).lower()
        version = match.group(2)
        server = "biorxiv" if _host_matches(host, "biorxiv.org") else "medrxiv"
        suffix = version or ""
        fingerprint = f"{server}:{value}"
        return PaperIdentity(
            kind=server,
            value=value,
            fingerprint=fingerprint,
            key=sha256_text(fingerprint),
            normalized_url=f"https://www.{server}.org/content/{value}{suffix}.full.pdf",
            version=version,
        )
    server = "biorxiv" if _host_matches(host, "biorxiv.org") else "medrxiv"
    canonical = canonicalize_url(f"https://www.{server}.org{path}")
    fingerprint = f"{server}:{canonical.lower()}"
    return PaperIdentity(
        kind=server,
        value=canonical,
        fingerprint=fingerprint,
        key=sha256_text(fingerprint),
        normalized_url=canonical,
    )


def _proceedings_identity(kind: str, host: str, path: str, *, suffix: str | None = None) -> PaperIdentity:
    clean = path.strip("/")
    if suffix and clean.lower().endswith(suffix):
        clean = clean[: -len(suffix)]
    if clean.lower().endswith(".html"):
        clean = clean[: -len(".html")]
    fingerprint = f"{kind}:{host}{clean}".lower()
    base = f"https://{host}/{clean}.pdf" if suffix else f"https://{host}/{clean}"
    return PaperIdentity(
        kind=kind,
        value=clean,
        fingerprint=fingerprint,
        key=sha256_text(fingerprint),
        normalized_url=base,
    )
