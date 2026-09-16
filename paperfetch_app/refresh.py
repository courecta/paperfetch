"""Backfill bibliographic metadata into bundles that already exist.

Metadata lookups fail independently of extraction: arXiv and Semantic Scholar
both throttle hard, and a paper ingested during a rate-limit window keeps its
text, figures and tables but ships with no authors or year. That is invisible
until the BibTeX comes out as `unknown0000title`.

Re-extracting to recover a handful of fields would cost minutes per paper and
re-run a layout model for nothing, so this refreshes metadata in place:
meta.json, refs.bib and the index entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bibtex import generate_bibtex
from .bundle import bundle_paths
from .discover import lookup_paper
from .fetch.http import HttpClient
from .identity import build_identity
from .io_utils import now_utc_iso, read_json, write_json_atomic

ENRICHED_FIELDS = ("title", "authors", "year", "venue", "abstract", "doi", "categories", "primary_category")


def needs_metadata(meta: dict[str, Any]) -> bool:
    return not meta.get("authors") or not meta.get("year")


def _from_arxiv(arxiv_id: str, client: HttpClient) -> dict[str, Any]:
    from .metadata import fetch_arxiv_metadata

    found = fetch_arxiv_metadata(arxiv_id, client=client, retries=1)
    return {k: v for k, v in found.to_dict().items() if v not in (None, "", [])}


def _from_s2(identity: Any, client: HttpClient) -> dict[str, Any]:
    from .citations import s2_paper_id

    paper = lookup_paper(s2_paper_id(identity), client=client, retries=1)
    if paper is None:
        return {}
    return {
        k: v
        for k, v in {
            "title": paper.title,
            "authors": paper.authors,
            "year": paper.year,
            "venue": paper.venue,
            "abstract": paper.abstract,
            "doi": paper.doi,
        }.items()
        if v not in (None, "", [])
    }


def refresh_key(
    library_dir: Path,
    key: str,
    *,
    client: HttpClient,
    force: bool = False,
) -> dict[str, Any]:
    """Refresh one bundle. Returns {status, reason?, fields?}."""
    paths = bundle_paths(library_dir, key)
    if not paths.meta.is_file():
        return {"status": "missing", "reason": "no meta.json"}

    meta = read_json(paths.meta) or {}
    if not force and not needs_metadata(meta):
        return {"status": "skipped", "reason": "already has authors and year"}

    url = meta.get("source_url") or meta.get("url") or ""
    if not url:
        return {"status": "failed", "reason": "bundle records no source url"}

    identity = build_identity(url)
    found: dict[str, Any] = {}
    errors: list[str] = []

    if identity.kind == "arxiv":
        try:
            found = _from_arxiv(identity.value, client)
        except Exception as exc:
            errors.append(f"arxiv: {exc}")
    if not found.get("authors"):
        try:
            found.update({k: v for k, v in _from_s2(identity, client).items() if k not in found})
        except Exception as exc:
            errors.append(f"semantic scholar: {exc}")

    if not found:
        return {"status": "failed", "reason": "; ".join(errors) or "no metadata found"}

    changed = [f for f in ENRICHED_FIELDS if found.get(f) and (force or not meta.get(f))]
    for field in changed:
        meta[field] = found[field]
    meta["updated_at"] = now_utc_iso()

    # The stored BibTeX was generated from the thin metadata, so it carries the
    # same gaps and has to be rebuilt rather than kept.
    bibtex = generate_bibtex(meta)
    if bibtex:
        meta["bibtex"] = bibtex
        paths.refs_bib.write_text(bibtex + "\n", encoding="utf-8")

    write_json_atomic(paths.meta, meta)
    return {"status": "updated", "fields": changed, "meta": meta}


def refresh_library(
    library_dir: Path,
    keys: list[str],
    *,
    force: bool = False,
    client: HttpClient | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    owns = client is None
    client = client or HttpClient(timeout=30, retries=1, backoff=2.0)
    try:
        return [(key, refresh_key(library_dir, key, client=client, force=force)) for key in keys]
    finally:
        if owns:
            client.close()
