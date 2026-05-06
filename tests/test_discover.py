from __future__ import annotations

import pytest
from paperfetch_app.discover import _to_discovered, format_discovered


class TestToDiscovered:
    def test_basic_paper(self):
        raw = {
            "paperId": "abc123",
            "title": "Test Paper",
            "authors": [{"name": "Alice Author"}],
            "year": 2024,
            "abstract": "This is a test.",
            "venue": "ICML",
            "openAccessPdf": {"url": "https://example.com/pdf.pdf"},
            "externalIds": {"ArXiv": "2401.00001"},
            "isOpenAccess": True,
            "citationCount": 42,
        }
        p = _to_discovered(raw)
        assert p is not None
        assert p.title == "Test Paper"
        assert p.authors == ["Alice Author"]
        assert p.year == 2024
        assert p.venue == "ICML"
        assert p.arxiv_id == "2401.00001"
        assert p.url == "https://arxiv.org/abs/2401.00001"
        assert p.is_open_access is True
        assert p.citation_count == 42

    def test_no_title_returns_none(self):
        raw = {"paperId": "abc123", "authors": []}
        assert _to_discovered(raw) is None

    def test_doi_fallback(self):
        raw = {
            "paperId": "abc123",
            "title": "DOI Paper",
            "authors": [],
            "externalIds": {"DOI": "10.1000/x"},
        }
        p = _to_discovered(raw)
        assert p.url == "https://doi.org/10.1000/x"

    def test_s2_fallback(self):
        raw = {
            "paperId": "abc123",
            "title": "S2 Paper",
            "authors": [],
        }
        p = _to_discovered(raw)
        assert "semanticscholar.org" in p.url

    def test_no_authors(self):
        raw = {
            "paperId": "abc123",
            "title": "No Authors",
            "authors": [],
        }
        p = _to_discovered(raw)
        assert p.authors == []

    def test_none_abstract(self):
        raw = {
            "paperId": "abc123",
            "title": "No Abstract",
            "authors": [],
            "abstract": None,
        }
        p = _to_discovered(raw)
        assert p.abstract is None

    def test_open_access_false(self):
        raw = {
            "paperId": "abc123",
            "title": "Closed",
            "authors": [],
            "isOpenAccess": False,
        }
        p = _to_discovered(raw)
        assert p.is_open_access is False

    def test_citation_count_none(self):
        raw = {
            "paperId": "abc123",
            "title": "No Citations",
            "authors": [],
        }
        p = _to_discovered(raw)
        assert p.citation_count is None


class TestFormatDiscovered:
    def test_urls_format(self):
        papers = [
            type("P", (), {"title": "A", "url": "https://a.com", "year": 2024, "venue": "V", "authors": [], "abstract": None, "arxiv_id": None, "doi": None, "is_open_access": True, "citation_count": None, "paper_id": "1"})(),
            type("P", (), {"title": "B", "url": "https://b.com", "year": 2023, "venue": "W", "authors": [], "abstract": None, "arxiv_id": None, "doi": None, "is_open_access": False, "citation_count": None, "paper_id": "2"})(),
        ]
        result = format_discovered(papers, "urls")
        assert "https://a.com" in result
        assert "https://b.com" in result

    def test_tsv_format(self):
        papers = [
            type("P", (), {"title": "A", "url": "https://a.com", "year": 2024, "venue": "V", "authors": [], "abstract": None, "arxiv_id": None, "doi": None, "is_open_access": True, "citation_count": None, "paper_id": "1"})(),
        ]
        result = format_discovered(papers, "tsv")
        assert "slug\ttitle\turl\tyear\tvenue" in result
        assert "a\tA\thttps://a.com\t2024\tV" in result

    def test_json_format(self):
        papers = [
            type("P", (), {"title": "A", "url": "https://a.com", "year": 2024, "venue": "V", "authors": [], "abstract": "abs", "arxiv_id": None, "doi": None, "is_open_access": True, "citation_count": None, "paper_id": "1"})(),
        ]
        result = format_discovered(papers, "json")
        import json
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["title"] == "A"
        assert data[0]["abstract"] == "abs"

    def test_invalid_format(self):
        papers = []
        with pytest.raises(ValueError, match="Unsupported"):
            format_discovered(papers, "invalid")

    def test_empty_list(self):
        result = format_discovered([], "urls")
        assert result == ""

    def test_no_url(self):
        papers = [
            type("P", (), {"title": "A", "url": None, "year": 2024, "venue": "V", "authors": [], "abstract": None, "arxiv_id": None, "doi": None, "is_open_access": True, "citation_count": None, "paper_id": "1"})(),
        ]
        result = format_discovered(papers, "urls")
        assert result.strip() == ""
