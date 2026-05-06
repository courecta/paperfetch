from __future__ import annotations

import pytest
from paperfetch_app.bibtex import _generate_bibtex, export_bibtex


class TestGenerateBibtex:
    def test_arxiv_paper(self):
        entry = {
            "title": "Test Paper",
            "authors": ["Alice Author", "Bob Author"],
            "year": 2024,
            "identity_kind": "arxiv",
            "identity_value": "2401.00001",
            "primary_category": "cs.LG",
            "source_url": "https://arxiv.org/abs/2401.00001",
        }
        result = _generate_bibtex(entry)
        assert "@misc{" in result
        assert "Test Paper" in result
        assert "Alice Author" in result
        assert "Bob Author" in result
        assert "year = {2024}" in result
        assert "eprint = {2401.00001}" in result
        assert "archivePrefix = {arXiv}" in result
        assert "primaryClass = {cs.LG}" in result

    def test_article_with_doi(self):
        entry = {
            "title": "Journal Article",
            "authors": ["Carol C."],
            "year": 2023,
            "identity_kind": "doi",
            "identity_value": "10.1000/xyz",
            "doi": "10.1000/xyz",
            "source_url": "https://doi.org/10.1000/xyz",
        }
        result = _generate_bibtex(entry)
        assert "@article{" in result
        assert "Journal Article" in result
        assert "doi = {10.1000/xyz}" in result

    def test_no_title(self):
        entry = {"authors": ["Alice"], "year": 2024}
        assert _generate_bibtex(entry) is None

    def test_no_authors(self):
        entry = {"title": "Solo Paper", "year": 2024}
        result = _generate_bibtex(entry)
        assert "title = {Solo Paper}" in result
        assert "author" not in result

    def test_year_none(self):
        entry = {"title": "No Year", "authors": ["Alice"]}
        result = _generate_bibtex(entry)
        assert "year" not in result

    def test_special_chars_in_title(self):
        entry = {"title": "Paper: A & B", "authors": ["Alice"], "year": 2024}
        result = _generate_bibtex(entry)
        assert "Paper: A & B" in result

    def test_long_author_list(self):
        entry = {
            "title": "Many Authors",
            "authors": [f"Author {i}" for i in range(20)],
            "year": 2024,
        }
        result = _generate_bibtex(entry)
        assert "Author 0" in result
        assert "Author 19" in result


class TestExportBibtex:
    def test_multiple_entries(self):
        entries = [
            {"title": "Paper 1", "authors": ["A1"], "year": 2024, "identity_kind": "arxiv", "identity_value": "2401.00001"},
            {"title": "Paper 2", "authors": ["A2"], "year": 2023, "identity_kind": "doi", "identity_value": "10.1000/x"},
        ]
        result = export_bibtex(entries)
        assert "@misc{" in result
        assert "Paper 1" in result
        assert "Paper 2" in result
        assert result.endswith("\n")

    def test_uses_existing_bibtex(self):
        entries = [
            {
                "title": "Paper 1",
                "bibtex": "@article{custom, title={Custom}}",
            }
        ]
        result = export_bibtex(entries)
        assert "@article{custom" in result
        assert "Custom" in result

    def test_empty_list(self):
        result = export_bibtex([])
        assert result == "\n"

    def test_mixed_bibtex_and_generated(self):
        entries = [
            {"title": "Gen", "authors": ["A"], "year": 2024},
            {"title": "Pre", "bibtex": "@misc{pre, title={Pre}}"},
        ]
        result = export_bibtex(entries)
        assert "Gen" in result
        assert "Pre" in result
