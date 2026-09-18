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


class TestMetadataRefresh:
    def test_bundle_without_authors_or_year_needs_refresh(self):
        from paperfetch_app.refresh import needs_metadata

        assert needs_metadata({"title": "T"})
        assert needs_metadata({"title": "T", "authors": ["A"]})  # no year
        assert needs_metadata({"title": "T", "year": 2026})  # no authors
        assert not needs_metadata({"title": "T", "authors": ["A"], "year": 2026})

    def test_refresh_reports_a_bundle_with_no_source_url(self, tmp_path):
        from paperfetch_app.bundle import bundle_paths
        from paperfetch_app.io_utils import write_json_atomic
        from paperfetch_app.refresh import refresh_key

        paths = bundle_paths(tmp_path, "k")
        paths.root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(paths.meta, {"title": "No URL"})
        result = refresh_key(tmp_path, "k", client=None, force=False)  # type: ignore[arg-type]
        assert result["status"] == "failed"
        assert "source url" in result["reason"]

    def test_refresh_skips_complete_metadata_without_calling_out(self, tmp_path):
        from paperfetch_app.bundle import bundle_paths
        from paperfetch_app.io_utils import write_json_atomic
        from paperfetch_app.refresh import refresh_key

        paths = bundle_paths(tmp_path, "k")
        paths.root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(paths.meta, {"title": "T", "authors": ["A"], "year": 2026, "source_url": "u"})
        # client=None would raise if the network were touched.
        result = refresh_key(tmp_path, "k", client=None, force=False)  # type: ignore[arg-type]
        assert result["status"] == "skipped"


class TestDiscoveredUrls:
    def test_null_paper_id_does_not_become_a_none_url(self):
        from paperfetch_app.discover import _to_discovered

        # Bibliography-extracted records carry paperId: null.
        paper = _to_discovered({"title": "A paper", "paperId": None})
        assert paper is not None
        assert paper.url is None
        assert paper.paper_id == ""

    def test_arxiv_id_wins_over_the_s2_page(self):
        from paperfetch_app.discover import _to_discovered

        paper = _to_discovered({"title": "T", "paperId": "abc", "externalIds": {"ArXiv": "2504.05662"}})
        assert paper.url == "https://arxiv.org/abs/2504.05662"

    def test_s2_page_is_used_when_a_paper_id_exists(self):
        from paperfetch_app.discover import _to_discovered

        paper = _to_discovered({"title": "T", "paperId": "abc123"})
        assert paper.url == "https://www.semanticscholar.org/paper/abc123"


class TestPublisherUrls:
    """Publisher URLs embed the DOI; reading it routes them to the DOI resolver."""

    @pytest.mark.parametrize(
        "url,doi",
        [
            ("https://link.springer.com/article/10.1007/s11263-021-01466-8", "10.1007/s11263-021-01466-8"),
            ("https://link.springer.com/chapter/10.1007/978-3-031-19821-2_29", "10.1007/978-3-031-19821-2_29"),
            ("https://onlinelibrary.wiley.com/doi/10.1002/aisy.202100116", "10.1002/aisy.202100116"),
            ("https://dl.acm.org/doi/10.1145/3503161.3548024", "10.1145/3503161.3548024"),
        ],
    )
    def test_doi_is_read_out_of_the_path(self, url, doi):
        identity = build_identity(url)
        assert identity.kind == "doi"
        assert identity.value == doi

    def test_publisher_url_and_bare_doi_are_the_same_paper(self):
        """Otherwise the same paper is ingested twice under two keys."""
        a = build_identity("https://link.springer.com/article/10.1007/s11263-021-01466-8")
        b = build_identity("https://doi.org/10.1007/s11263-021-01466-8")
        assert a.key == b.key

    def test_unknown_publisher_is_left_as_a_url(self):
        identity = build_identity("https://example.com/some/article")
        assert identity.kind == "url"


class TestPreprintFallback:
    def test_arxiv_id_becomes_the_preferred_pdf_candidate(self):
        """Publishers serve paywalls or anti-bot pages; arXiv serves the PDF."""
        from paperfetch_app.resolve.doi import _s2_pdf

        class FakeClient:
            def get_json(self, url, **kwargs):
                return {
                    "title": "A paper",
                    "year": 2021,
                    "openAccessPdf": None,
                    "externalIds": {"ArXiv": "1908.00682", "DOI": "10.1007/x"},
                }

        pdf, meta = _s2_pdf("10.1007/x", FakeClient())
        assert pdf is None
        assert meta["arxiv_id"] == "1908.00682"
