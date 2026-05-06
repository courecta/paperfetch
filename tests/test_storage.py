from __future__ import annotations

import json
from pathlib import Path

import pytest
from paperfetch_app.storage import (
    clean_library,
    ensure_library_paths,
    index_path,
    list_entries,
    load_index,
    materialize,
    save_index,
)


class TestEnsureLibraryPaths:
    def test_returns_correct_paths(self, tmp_path: Path):
        store = ensure_library_paths(tmp_path, "paper-abc123")
        assert store.pdf == tmp_path / "pdfs" / "paper-abc123.pdf"
        assert store.md == tmp_path / "md" / "paper-abc123.md"
        assert store.meta == tmp_path / "meta" / "paper-abc123.json"


class TestLoadIndex:
    def test_missing_file(self, tmp_path: Path):
        result = load_index(tmp_path / "index.json")
        assert result == {}

    def test_valid_json(self, tmp_path: Path):
        f = tmp_path / "index.json"
        f.write_text(json.dumps({"key1": {"title": "Paper 1"}}))
        result = load_index(f)
        assert result == {"key1": {"title": "Paper 1"}}

    def test_invalid_json(self, tmp_path: Path):
        f = tmp_path / "index.json"
        f.write_text("not json")
        result = load_index(f)
        assert result == {}

    def test_non_dict_json(self, tmp_path: Path):
        f = tmp_path / "index.json"
        f.write_text("[1, 2, 3]")
        result = load_index(f)
        assert result == {}

    def test_filters_non_dict_values(self, tmp_path: Path):
        f = tmp_path / "index.json"
        f.write_text(json.dumps({"key1": {"title": "ok"}, "key2": "not_a_dict", 3: "bad_key"}))
        result = load_index(f)
        assert result == {"key1": {"title": "ok"}}


class TestSaveIndex:
    def test_roundtrip(self, tmp_path: Path):
        f = tmp_path / "index.json"
        data = {"key1": {"title": "Paper 1"}}
        save_index(f, data)
        result = load_index(f)
        assert result == data


class TestListEntries:
    def test_empty(self):
        assert list_entries({}) == []

    def test_sorting(self):
        index = {
            "a": {"updated_at": "2024-01-01T00:00:00+00:00"},
            "b": {"updated_at": "2024-03-01T00:00:00+00:00"},
            "c": {"updated_at": "2024-02-01T00:00:00+00:00"},
        }
        rows = list_entries(index)
        assert [k for k, _ in rows] == ["b", "c", "a"]

    def test_limit(self):
        index = {
            "a": {"updated_at": "2024-01-01T00:00:00+00:00"},
            "b": {"updated_at": "2024-03-01T00:00:00+00:00"},
            "c": {"updated_at": "2024-02-01T00:00:00+00:00"},
        }
        rows = list_entries(index, limit=2)
        assert len(rows) == 2
        assert [k for k, _ in rows] == ["b", "c"]

    def test_no_limit(self):
        index = {f"k{i}": {"updated_at": f"2024-01-{i:02d}T00:00:00+00:00"} for i in range(1, 101)}
        rows = list_entries(index)
        assert len(rows) == 100


class TestMaterialize:
    def test_copy(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")
        materialize(src, dst, mode="copy", overwrite=False)
        assert dst.exists()
        assert dst.read_text() == "hello"
        assert not dst.is_symlink()

    def test_symlink(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")
        materialize(src, dst, mode="symlink", overwrite=False)
        assert dst.exists()
        assert dst.is_symlink()
        assert dst.read_text() == "hello"

    def test_hardlink(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")
        materialize(src, dst, mode="hardlink", overwrite=False)
        assert dst.exists()
        assert dst.stat().st_ino == src.stat().st_ino

    def test_overwrite_false_skips(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("new")
        dst.write_text("old")
        materialize(src, dst, mode="copy", overwrite=False)
        assert dst.read_text() == "old"

    def test_overwrite_true_replaces(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("new")
        dst.write_text("old")
        materialize(src, dst, mode="copy", overwrite=True)
        assert dst.read_text() == "new"

    def test_unsupported_mode(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")
        with pytest.raises(ValueError, match="Unsupported"):
            materialize(src, dst, mode="invalid", overwrite=False)


class TestCleanLibrary:
    def test_prune_missing(self, tmp_path: Path):
        idx = {
            "a": {"pdf": "pdfs/a.pdf", "md": "md/a.md"},
            "b": {"pdf": "pdfs/b.pdf", "md": "md/b.md"},
        }
        (tmp_path / "pdfs").mkdir()
        (tmp_path / "md").mkdir()
        (tmp_path / "pdfs" / "a.pdf").write_text("pdf")
        (tmp_path / "md" / "a.md").write_text("md")
        # b files don't exist

        result = clean_library(tmp_path, idx, prune_missing_entries=True, remove_orphans=False)
        assert result["removed_index_entries"] == 1
        assert "b" not in idx
        assert "a" in idx

    def test_remove_orphans(self, tmp_path: Path):
        idx = {"a": {"pdf": "pdfs/a.pdf", "md": "md/a.md"}}
        (tmp_path / "pdfs").mkdir()
        (tmp_path / "md").mkdir()
        (tmp_path / "pdfs" / "a.pdf").write_text("pdf")
        (tmp_path / "md" / "a.md").write_text("md")
        # orphan file
        (tmp_path / "pdfs" / "orphan.pdf").write_text("orphan")

        result = clean_library(tmp_path, idx, prune_missing_entries=False, remove_orphans=True)
        assert result["removed_orphans"] == 1
        assert not (tmp_path / "pdfs" / "orphan.pdf").exists()

    def test_keep_meta(self, tmp_path: Path):
        idx = {"a": {"pdf": "pdfs/a.pdf", "md": "md/a.md"}}
        (tmp_path / "pdfs").mkdir()
        (tmp_path / "md").mkdir()
        (tmp_path / "meta").mkdir()
        (tmp_path / "pdfs" / "a.pdf").write_text("pdf")
        (tmp_path / "md" / "a.md").write_text("md")
        # meta file should not be considered orphan
        (tmp_path / "meta" / "extra.json").write_text("{}")

        result = clean_library(tmp_path, idx, prune_missing_entries=False, remove_orphans=True)
        assert result["removed_orphans"] == 0
        assert (tmp_path / "meta" / "extra.json").exists()

    def test_nothing_to_do(self, tmp_path: Path):
        idx = {"a": {"pdf": "pdfs/a.pdf", "md": "md/a.md"}}
        (tmp_path / "pdfs").mkdir()
        (tmp_path / "md").mkdir()
        (tmp_path / "pdfs" / "a.pdf").write_text("pdf")
        (tmp_path / "md" / "a.md").write_text("md")

        result = clean_library(tmp_path, idx, prune_missing_entries=False, remove_orphans=False)
        assert result == {"removed_index_entries": 0, "removed_orphans": 0}
