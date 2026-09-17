"""Regressions for defects found in the whole-project review."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from paperfetch_app.errors import PaperfetchError
from paperfetch_app.storage import IndexUnreadableError, clean_library


class TestCleanCannotDestroyTheLibrary:
    @pytest.mark.parametrize("index", [{}, {"aaa": {}}], ids=["index-lost", "index-partial"])
    def test_a_real_bundle_is_never_an_orphan(self, tmp_path: Path, index):
        """Disk is the library; index.json is a derived view of it.

        A truncated index destroyed the whole library and a partially written
        one destroyed part of it, because "absent from the index" meant delete.
        """
        for key in ("aaa", "bbb"):
            (tmp_path / key).mkdir()
            (tmp_path / key / "meta.json").write_text("{}")

        result = clean_library(tmp_path, index, prune_missing_entries=False, remove_orphans=True)
        assert (tmp_path / "aaa" / "meta.json").exists()
        assert (tmp_path / "bbb" / "meta.json").exists()
        assert result["removed_orphans"] == 0
        # They are reported for reindexing rather than deleted.
        assert "bbb" in result["unindexed_bundles"]

    def test_a_directory_with_no_meta_is_still_removed(self, tmp_path: Path):
        junk = tmp_path / "abandoned"
        junk.mkdir()
        (junk / "partial.tmp").write_text("x")
        result = clean_library(tmp_path, {}, prune_missing_entries=False, remove_orphans=True)
        assert not junk.exists()
        assert result["removed_orphans"] == 1

    def test_unreadable_index_raises_before_anything_is_deleted(self, tmp_path: Path):
        from paperfetch_app.storage import load_index

        (tmp_path / "index.json").write_text("{truncated")
        with pytest.raises(IndexUnreadableError):
            load_index(tmp_path / "index.json")

    def test_recent_staging_is_left_for_the_ingest_that_owns_it(self, tmp_path: Path):
        live = tmp_path / ".staging" / "key-abc"
        live.mkdir(parents=True)
        clean_library(tmp_path, {"k": {}}, prune_missing_entries=False, remove_orphans=False)
        # A marker run can hold staging for minutes; clean takes no per-key lock.
        assert live.exists()


class TestHttpErrorsAreTyped:
    def test_non_retriable_status_raises_paperfetch_error(self):
        """requests.HTTPError is outside the hierarchy, so resolver guards missed it."""
        import requests

        from paperfetch_app.errors import DownloadError
        from paperfetch_app.fetch.http import HttpClient

        response = requests.Response()
        response.status_code = 404
        with pytest.raises(DownloadError):
            HttpClient._check_status(response, "https://example.com/x")
        assert issubclass(DownloadError, PaperfetchError)


class TestPositionalFloatLookup:
    def test_figure_two_is_not_figure_ten(self, tmp_path: Path):
        from paperfetch_app.db import _positional

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE figures (id TEXT, paper_key TEXT)")
        for fid in ("fig-1", "fig-2", "fig-3", "fig-10", "fig-11"):
            conn.execute("INSERT INTO figures VALUES (?,?)", (fid, "k"))
        rows = conn.execute("SELECT * FROM figures WHERE paper_key=?", ("k",)).fetchall()
        # Lexicographic order put fig-10 second.
        assert _positional(rows, "2")["id"] == "fig-2"
        # fig-10 is the 4th float in document order, not the 10th.
        assert _positional(rows, "4")["id"] == "fig-10"
        assert _positional(rows, "99") is None
        conn.close()


class TestS2NullFields:
    def test_null_authors_do_not_raise(self):
        from paperfetch_app.discover import _to_discovered

        paper = _to_discovered({"title": "X", "authors": None, "paperId": None})
        assert paper is not None and paper.authors == []

    def test_unidentified_papers_do_not_collapse_into_one_rank(self):
        from paperfetch_app.citations import rank_by_frequency
        from paperfetch_app.discover import DiscoveredPaper

        def p(pid: str, title: str) -> DiscoveredPaper:
            return DiscoveredPaper(pid, title, [], 2025, None, None, None, None, None, True, 0)

        group = [p("", "Alpha"), p("", "Beta"), p("real", "Gamma")]
        ranked = rank_by_frequency([group, group])
        # Previously all three collapsed onto "" and an arbitrary one ranked #1.
        assert [title for paper, _ in ranked for title in [paper.title]] == ["Gamma"]


class TestMarkerNaming:
    @pytest.mark.parametrize(
        "raw,expected_suffix",
        [("_page_0_Figure_1.jpeg", ".jpeg"), ("fig.jpg", ".jpg"), ("plain", ".png")],
    )
    def test_real_image_extension_is_kept(self, raw, expected_suffix):
        from paperfetch_app.extract.marker_ir import _safe_name

        assert _safe_name(raw).endswith(expected_suffix)


class TestCoverageIsIdempotent:
    def test_second_audit_matches_the_first(self):
        from paperfetch_app.extract.coverage import audit
        from paperfetch_app.extract.ir import Block, Document, Inline

        doc = Document(
            schema_version=1,
            key="k",
            source_kind="x",
            source_url="u",
            blocks=[Block(kind="paragraph", inlines=[Inline(kind="text", text="word " * 400)])],
        )
        doc.coverage = {
            "source_counts": {"figures": 2},
            "mapped_counts": {"figures": 2},
            "assets": {"images_total": 10, "images_downloaded": 4},
        }
        first = dict(audit(doc))
        second = audit(doc)
        # Dropping `assets` made a failing ratio silently pass on re-audit.
        assert (second["ratio"], second["ok"]) == (first["ratio"], first["ok"])
        assert second["ok"] is False


class TestManifestTitles:
    def test_null_title_does_not_become_the_string_none(self):
        from paperfetch_app.manifests import _coerce_paper_dict

        # csv.DictReader fills a short row with None, and .get(k, default)
        # does not apply the default when the key exists.
        entry = _coerce_paper_dict({"url": "https://arxiv.org/abs/2411.00278", "title": None}, "test")
        assert entry is not None
        assert entry.title != "None" and entry.slug != "none"
