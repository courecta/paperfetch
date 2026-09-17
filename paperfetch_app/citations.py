"""Citation-graph traversal over the Semantic Scholar Academic Graph.

Returns the same DiscoveredPaper shape as `discover`, so a traversal can be
written straight out as a manifest and fed back into `fetch`: pull the papers
a reading list cites, or the ones citing it, without assembling URLs by hand.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from .discover import S2_FIELDS, DiscoveredPaper, _to_discovered
from .errors import PaperfetchError
from .fetch.http import HttpClient
from .models import PaperIdentity

S2_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper"

Direction = Literal["references", "citations"]


def s2_paper_id(identity: PaperIdentity) -> str:
    """Map a paperfetch identity onto a Semantic Scholar paper id."""
    if identity.kind == "arxiv":
        return f"arXiv:{identity.value}"
    if identity.kind == "doi":
        return f"DOI:{identity.value}"
    raise PaperfetchError(
        f"Semantic Scholar cannot be queried for {identity.kind} identities",
        hint="Citation traversal needs an arXiv id or a DOI.",
    )


def _page(
    paper_id: str,
    direction: Direction,
    limit: int,
    client: HttpClient,
) -> list[dict[str, Any]]:
    # The graph API nests each neighbour under citedPaper/citingPaper.
    nested_key = "citedPaper" if direction == "references" else "citingPaper"
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < limit:
        want = min(100, limit - len(rows))
        raw = client.get_text(
            f"{S2_PAPER_URL}/{paper_id}/{direction}",
            params={"fields": S2_FIELDS, "limit": want, "offset": offset},
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PaperfetchError(f"Semantic Scholar returned invalid JSON: {exc}") from exc

        batch = payload.get("data") or []
        if not batch:
            break
        for item in batch:
            nested = item.get(nested_key)
            if isinstance(nested, dict):
                rows.append(nested)
        if payload.get("next") is None:
            break
        offset = int(payload["next"])
    return rows[:limit]


def related_papers(
    identity: PaperIdentity,
    *,
    direction: Direction = "references",
    limit: int = 50,
    open_access_only: bool = False,
    min_citations: int = 0,
    client: HttpClient | None = None,
) -> list[DiscoveredPaper]:
    """Papers this one cites (references) or that cite it (citations)."""
    owns = client is None
    client = client or HttpClient(timeout=30, retries=2, backoff=1.5)
    try:
        rows = _page(s2_paper_id(identity), direction, limit, client)
    finally:
        if owns:
            client.close()

    papers: list[DiscoveredPaper] = []
    for row in rows:
        paper = _to_discovered(row)
        if paper is None:
            continue
        if open_access_only and not paper.is_open_access:
            continue
        if min_citations and (paper.citation_count or 0) < min_citations:
            continue
        papers.append(paper)
    return papers


def rank_by_frequency(groups: list[list[DiscoveredPaper]]) -> list[tuple[DiscoveredPaper, int]]:
    """Rank neighbours by how many seed papers reached them.

    A work cited by many papers in a reading list is the field's shared
    foundation, which is usually what you want to read next.
    """
    counts: dict[str, int] = {}
    best: dict[str, DiscoveredPaper] = {}
    for group in groups:
        # Records S2 extracted from a bibliography carry no paperId. Keying on
        # "" would collapse them all into one entry whose count is the sum of
        # unrelated papers, putting an arbitrary one at rank 1.
        identified = {p.paper_id: p for p in group if p.paper_id}
        for paper in identified.values():
            counts[paper.paper_id] = counts.get(paper.paper_id, 0) + 1
            best.setdefault(paper.paper_id, paper)
    ranked = [(best[pid], count) for pid, count in counts.items()]
    ranked.sort(key=lambda pair: (-pair[1], -(pair[0].citation_count or 0)))
    return ranked
