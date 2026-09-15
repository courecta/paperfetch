from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperfetch_app.server import create_app


@pytest.fixture
def empty_library(tmp_path: Path):
    return tmp_path


@pytest.fixture
def populated_library(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "pdfs").mkdir()
    (lib / "md").mkdir()
    (lib / "meta").mkdir()

    index = {
        "key1": {
            "slug": "paper1",
            "title": "Paper One",
            "source_url": "https://arxiv.org/abs/2401.00001",
            "identity_kind": "arxiv",
            "identity_value": "2401.00001",
            "identity_fingerprint": "arxiv:2401.00001",
            "pdf": "pdfs/paper1-key1.pdf",
            "md": "md/paper1-key1.md",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "authors": ["Alice"],
            "abstract": "Abstract one",
            "year": 2024,
            "categories": ["cs.LG"],
            "extractor": "arxiv_html",
        },
        "key2": {
            "slug": "paper2",
            "title": "Paper Two",
            "source_url": "https://arxiv.org/abs/2401.00002",
            "identity_kind": "arxiv",
            "identity_value": "2401.00002",
            "identity_fingerprint": "arxiv:2401.00002",
            "pdf": "pdfs/paper2-key2.pdf",
            "md": "md/paper2-key2.md",
            "updated_at": "2024-02-01T00:00:00+00:00",
            "authors": ["Bob"],
            "abstract": "Abstract two",
            "year": 2023,
            "categories": ["cs.CV"],
            "extractor": "marker",
        },
    }

    # Write index
    (lib / "index.json").write_text(json.dumps(index))

    # Write md content
    (lib / "md" / "paper1-key1.md").write_text("# Paper One\n\nContent here.")
    (lib / "md" / "paper2-key2.md").write_text("# Paper Two\n\nMore content.")

    # Write pdfs
    (lib / "pdfs" / "paper1-key1.pdf").write_text("pdf1")
    (lib / "pdfs" / "paper2-key2.pdf").write_text("pdf2")

    return lib


class TestLibraryStats:
    def test_empty_library(self, empty_library: Path):
        (empty_library / "index.json").write_text("{}")
        app = create_app(empty_library)
        client = TestClient(app)
        resp = client.get("/api/v1/library/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_papers"] == 0
        assert data["by_extractor"] == {}
        assert data["by_year"] == {}

    def test_populated_library(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/library/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_papers"] == 2
        assert data["by_extractor"]["arxiv_html"] == 1
        assert data["by_extractor"]["marker"] == 1
        assert data["by_year"]["2024"] == 1
        assert data["by_year"]["2023"] == 1
        assert "cs.LG" in data["by_category"]
        assert "cs.CV" in data["by_category"]


class TestListPapers:
    def test_basic_list(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/papers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["papers"]) == 2

    def test_pagination(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/papers?limit=1&offset=0")
        data = resp.json()
        assert len(data["papers"]) == 1
        assert data["total"] == 2

        resp = client.get("/api/v1/papers?limit=1&offset=1")
        data = resp.json()
        assert len(data["papers"]) == 1

    def test_search(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/papers?query=One")
        data = resp.json()
        assert data["total"] == 1
        assert data["papers"][0]["title"] == "Paper One"

    def test_search_abstract(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/papers?query=Abstract%20two")
        data = resp.json()
        assert data["total"] == 1
        assert data["papers"][0]["title"] == "Paper Two"

    def test_search_no_results(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/papers?query=nonexistent")
        data = resp.json()
        assert data["total"] == 0


class TestGetPaper:
    def test_found(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/papers/key1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Paper One"
        assert "# Paper One" in data["markdown"]

    def test_not_found(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/papers/nonexistent")
        assert resp.status_code == 404

    def test_missing_markdown(self, populated_library: Path):
        # Delete markdown file
        (populated_library / "md" / "paper1-key1.md").unlink()
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/papers/key1")
        assert resp.status_code == 200
        data = resp.json()
        assert "markdown" not in data


class TestExportBibtex:
    def test_export_all(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/export/bibtex?all=true")
        assert resp.status_code == 200
        assert "@misc{" in resp.text
        assert "Paper One" in resp.text
        assert "Paper Two" in resp.text
        assert resp.headers["content-type"] == "application/x-bibtex"

    def test_export_by_key(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/export/bibtex?key=key1")
        assert resp.status_code == 200
        assert "Paper One" in resp.text
        assert "Paper Two" not in resp.text

    def test_no_keys(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/export/bibtex")
        assert resp.status_code == 400

    def test_invalid_key(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.get("/api/v1/export/bibtex?key=nonexistent")
        assert resp.status_code == 404


@pytest.mark.network
class TestDiscoverEndpoint:
    def test_discover_json(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.post("/api/v1/discover?query=machine%20learning&limit=3&output_format=json")
        # May get 429 or 200 depending on rate limits
        assert resp.status_code in (200, 429)

    def test_discover_urls(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.post("/api/v1/discover?query=AI&limit=2&output_format=urls")
        assert resp.status_code in (200, 429)

    def test_discover_tsv(self, populated_library: Path):
        app = create_app(populated_library)
        client = TestClient(app)
        resp = client.post("/api/v1/discover?query=deep%20learning&limit=2&output_format=tsv")
        assert resp.status_code in (200, 429)
