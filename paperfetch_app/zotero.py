"""Read a Zotero library as an ingestion source.

Zotero is where most researchers' reading lists already live, and it knows
things a URL does not: which collection a paper belongs to, what tags it
carries, whether it has been read. This turns those items into fetchable
papers so a curated library can be ingested losslessly without re-collecting
it by hand.

Reads run against the local API that Zotero 7 serves on port 23119, so nothing
touches the network and no API key is needed. Enable it under
Settings -> Advanced -> "Allow other applications on this computer to
communicate with Zotero".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import PaperfetchError
from .io_utils import safe_slug

ARXIV_IN_TEXT = re.compile(r"arxiv[:\s/]*((?:\d{4}\.\d{4,5})(?:v\d+)?)", re.IGNORECASE)
DOI_IN_TEXT = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ZoteroPaper:
    key: str
    title: str
    url: str
    collections: list[str]
    tags: list[str]

    @property
    def slug(self) -> str:
        return safe_slug(self.title, max_length=60) or self.key.lower()


def _client(library_id: str, library_type: str, api_key: str | None, local: bool):
    try:
        from pyzotero import zotero
    except ImportError as exc:  # pragma: no cover - import guard
        raise PaperfetchError(
            "pyzotero is required to read a Zotero library",
            hint='Install it with: pip install "paperfetch-tool[zotero]"',
        ) from exc
    if local:
        # The local API serves the logged-in user's library and ignores creds.
        return zotero.Zotero(library_id or "0", library_type, local=True)
    if not api_key:
        raise PaperfetchError(
            "a Zotero API key is required for the web API",
            hint="Pass --api-key, or drop --web to read the local library instead.",
        )
    return zotero.Zotero(library_id, library_type, api_key)


def _best_url(data: dict[str, Any]) -> str | None:
    """Prefer a resolvable identifier over whatever URL the item happens to hold."""
    doi = str(data.get("DOI") or "").strip()
    if doi:
        return f"https://doi.org/{doi.removeprefix('https://doi.org/')}"

    # Zotero stores arXiv ids inconsistently: Extra, Publication, or the URL.
    haystack = " ".join(
        str(data.get(field) or "")
        for field in ("extra", "archiveID", "publicationTitle", "repository", "url", "archive")
    )
    arxiv = ARXIV_IN_TEXT.search(haystack)
    if arxiv:
        return f"https://arxiv.org/abs/{arxiv.group(1)}"
    found_doi = DOI_IN_TEXT.search(haystack)
    if found_doi:
        return f"https://doi.org/{found_doi.group(1)}"

    url = str(data.get("url") or "").strip()
    return url or None


def _collection_names(client: Any) -> dict[str, str]:
    """Map collection key -> name.

    Errors propagate: swallowing a connection refusal here reported "no
    collection named X" when the real problem was that Zotero was not running.
    """
    return {c["key"]: c["data"]["name"] for c in client.everything(client.collections())}


def read_library(
    *,
    library_id: str = "0",
    library_type: str = "user",
    api_key: str | None = None,
    local: bool = True,
    collection: str | None = None,
    tag: str | None = None,
    limit: int | None = None,
) -> tuple[list[ZoteroPaper], list[tuple[str, str]]]:
    """Return (fetchable papers, skipped (title, reason) pairs)."""
    client = _client(library_id, library_type, api_key, local)

    try:
        names = _collection_names(client)
        if collection:
            matches = [key for key, name in names.items() if name.lower() == collection.lower()]
            if not matches:
                known = ", ".join(sorted(names.values())) or "none found"
                raise PaperfetchError(f"No Zotero collection named {collection!r}", hint=f"Collections: {known}")
            items = client.everything(client.collection_items(matches[0]))
        elif tag:
            items = client.everything(client.items(tag=tag))
        else:
            items = client.everything(client.items())
    except PaperfetchError:
        raise
    except Exception as exc:
        raise PaperfetchError(
            f"could not read the Zotero library: {exc}",
            hint=(
                "Is Zotero running with the local API enabled? "
                'Settings -> Advanced -> "Allow other applications on this computer to communicate with Zotero".'
            ),
        ) from exc

    papers: list[ZoteroPaper] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()

    for item in items:
        data = item.get("data") or {}
        if data.get("itemType") in {"attachment", "note", "annotation"}:
            continue
        title = str(data.get("title") or "").strip()
        if not title:
            continue

        url = _best_url(data)
        if not url:
            skipped.append((title, "no DOI, arXiv id or URL"))
            continue
        if url in seen:
            continue
        seen.add(url)

        papers.append(
            ZoteroPaper(
                key=str(data.get("key") or item.get("key") or ""),
                title=title,
                url=url,
                collections=[names.get(c, c) for c in (data.get("collections") or [])],
                tags=[str(t.get("tag")) for t in (data.get("tags") or []) if t.get("tag")],
            )
        )
        if limit and len(papers) >= limit:
            break

    return papers, skipped
