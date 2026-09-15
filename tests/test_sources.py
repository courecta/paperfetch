"""Zotero and citation-graph ingestion sources."""

from __future__ import annotations

import pytest

from paperfetch_app.citations import rank_by_frequency, s2_paper_id
from paperfetch_app.discover import DiscoveredPaper
from paperfetch_app.errors import PaperfetchError
from paperfetch_app.identity import build_identity
from paperfetch_app.zotero import _best_url


def _paper(pid: str, citations: int = 0) -> DiscoveredPaper:
    return DiscoveredPaper(
        paper_id=pid,
        title=f"Paper {pid}",
        authors=[],
        year=2025,
        abstract=None,
        venue=None,
        url=f"https://arxiv.org/abs/{pid}",
        arxiv_id=pid,
        doi=None,
        is_open_access=True,
        citation_count=citations,
    )


class TestZoteroUrlChoice:
    def test_doi_wins_over_a_stored_url(self):
        # The stored URL is often a paywalled landing page; the DOI resolves.
        assert _best_url({"DOI": "10.1109/CVPR.2023.1", "url": "https://ieeexplore.ieee.org/x"}) == (
            "https://doi.org/10.1109/CVPR.2023.1"
        )

    def test_doi_is_not_double_prefixed(self):
        assert _best_url({"DOI": "https://doi.org/10.1/x"}) == "https://doi.org/10.1/x"

    @pytest.mark.parametrize(
        "data",
        [
            {"extra": "arXiv:2504.05662"},
            {"publicationTitle": "arXiv preprint arXiv:2504.05662"},
            {"archiveID": "arXiv:2504.05662"},
        ],
        ids=["extra", "publication", "archiveID"],
    )
    def test_arxiv_id_is_recovered_from_free_text_fields(self, data):
        # Zotero stores the id in whichever field the import happened to use.
        assert _best_url(data) == "https://arxiv.org/abs/2504.05662"

    def test_arxiv_urls_are_left_for_identity_resolution(self):
        # No need to rewrite these here: build_identity already maps
        # /pdf/ and /abs/ onto the same arXiv identity.
        url = "https://arxiv.org/pdf/2504.05662"
        assert _best_url({"url": url}) == url

    def test_bare_url_is_the_last_resort(self):
        assert _best_url({"url": "https://example.com/paper"}) == "https://example.com/paper"

    def test_item_with_no_identifier_is_unfetchable(self):
        assert _best_url({"title": "Only a title"}) is None


class TestCitationSeeds:
    def test_arxiv_and_doi_map_to_s2_ids(self):
        assert s2_paper_id(build_identity("https://arxiv.org/abs/2504.05662")) == "arXiv:2504.05662"
        assert s2_paper_id(build_identity("https://doi.org/10.1/x")) == "DOI:10.1/x"

    def test_unsupported_identity_explains_itself(self):
        with pytest.raises(PaperfetchError, match="cannot be queried for url"):
            s2_paper_id(build_identity("https://example.com/some/paper"))


class TestRanking:
    def test_papers_cited_by_more_seeds_rank_first(self):
        shared, niche = _paper("1111.1111"), _paper("2222.2222")
        ranked = rank_by_frequency([[shared, niche], [shared], [shared]])
        assert [p.paper_id for p, _ in ranked] == ["1111.1111", "2222.2222"]
        assert dict((p.paper_id, n) for p, n in ranked)["1111.1111"] == 3

    def test_citation_count_breaks_ties(self):
        ranked = rank_by_frequency([[_paper("a", citations=5), _paper("b", citations=99)]])
        assert [p.paper_id for p, _ in ranked] == ["b", "a"]

    def test_a_seed_citing_the_same_paper_twice_counts_once(self):
        dup = _paper("1111.1111")
        ranked = rank_by_frequency([[dup, dup]])
        assert ranked[0][1] == 1
