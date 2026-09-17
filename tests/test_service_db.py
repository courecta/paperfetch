from __future__ import annotations

import json
from pathlib import Path

import pytest

from paperfetch_app import service
from paperfetch_app.bundle import bundle_paths
from paperfetch_app.extract.ir import Block, Cell, Document, Figure, ImageRef, Inline, Table
from paperfetch_app.io_utils import write_json_atomic
from paperfetch_app.migrate import migrate_library
from paperfetch_app.storage import index_path, load_index


def _make_bundle(library: Path, key: str = "abc123") -> Path:
    document = Document(
        schema_version=1,
        key=key,
        source_kind="arxiv_html",
        source_url="https://arxiv.org/abs/2401.00001",
        title="A Test Paper",
        blocks=[
            Block(kind="heading", id="sec-1", level=1, inlines=[Inline(kind="text", text="1 Introduction")]),
            Block(kind="paragraph", id="p1", inlines=[Inline(kind="text", text="We study anomaly detection in time series.")]),
            Block(
                kind="table",
                id="S4.T1",
                table=Table(
                    id="S4.T1",
                    label="Table 1",
                    caption=[Inline(kind="text", text="Results")],
                    header_rows=1,
                    rows=[
                        [Cell(inlines=[Inline(kind="text", text="Metric")], header=True), Cell(inlines=[Inline(kind="text", text="Score")], header=True)],
                        [Cell(inlines=[Inline(kind="text", text="F1")]), Cell(inlines=[Inline(kind="text", text="0.95")])],
                    ],
                ),
            ),
            Block(
                kind="figure",
                id="S4.F1",
                figure=Figure(
                    id="S4.F1",
                    label="Figure 1",
                    caption=[Inline(kind="text", text="Architecture")],
                    images=[ImageRef(src="https://example.com/a.png", local=f"figures/{key}-1.png")],
                ),
            ),
            Block(kind="math", id="S2.E1", tex="a=b", meta={"label": "(1)", "display": True}),
        ],
    )
    paths = bundle_paths(library, key)
    paths.figures_dir.mkdir(parents=True)
    (paths.figures_dir / f"{key}-1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    paths.root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(paths.document, document.to_dict())
    write_json_atomic(
        paths.meta,
        {
            "key": key,
            "title": "A Test Paper",
            "identity_kind": "arxiv",
            "identity_value": "2401.00001",
            "identity_fingerprint": "arxiv:2401.00001",
            "source_url": "https://arxiv.org/abs/2401.00001",
            "extractor": "arxiv_html",
            "year": 2024,
            "authors": ["Alice Author"],
            "coverage": {"ratio": 1.0, "ok": True},
        },
    )
    paths.markdown.write_text(
        "# 1 Introduction\n\nWe study **anomaly detection** in time series.\n\n## 2 Method\n\nDetails.\n",
        encoding="utf-8",
    )
    return paths.root


def test_reindex_and_stats(tmp_path: Path):
    _make_bundle(tmp_path)
    summary = service.reindex(tmp_path)
    assert summary["indexed"] == 1
    stats = service.stats(tmp_path)
    assert stats["total_papers"] == 1
    assert stats["tables"] == 1
    assert stats["figures"] == 1
    assert stats["equations"] == 1


def test_outline_and_read_section(tmp_path: Path):
    _make_bundle(tmp_path)
    sections = service.outline(tmp_path, "abc123")
    assert any("Introduction" in section["title"] for section in sections)
    result = service.read(tmp_path, "abc123", section="sec-1")
    assert "anomaly detection" in result["text"]


def test_grep(tmp_path: Path):
    _make_bundle(tmp_path)
    hits = service.grep(tmp_path, "anomaly")
    assert hits
    assert hits[0]["paper_key"] == "abc123"


def test_table_sidecar_fallback(tmp_path: Path):
    _make_bundle(tmp_path)
    service.reindex(tmp_path)
    record = service.get_table(tmp_path, "abc123", "Table 1")
    assert record is not None
    assert record.get("html_text") and "Metric" in record["html_text"]


def test_figure_lookup_by_label(tmp_path: Path):
    _make_bundle(tmp_path)
    service.reindex(tmp_path)
    record = service.get_figure(tmp_path, "abc123", "Figure 1")
    assert record is not None
    assert record["image_paths"] and Path(record["image_paths"][0]).is_file()


def test_read_pagination(tmp_path: Path):
    _make_bundle(tmp_path)
    result = service.read(tmp_path, "abc123", max_chars=20)
    assert result["offset"] == 0
    assert result["next_offset"] == 20
    assert len(result["text"]) == 20


def test_migrate_legacy_library(tmp_path: Path):
    (tmp_path / "md").mkdir()
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "meta").mkdir()
    (tmp_path / "md" / "legacy-key1.md").write_text("# Legacy\n\nOld content.\n", encoding="utf-8")
    (tmp_path / "pdfs" / "legacy-key1.pdf").write_bytes(b"%PDF-1.4")
    index = {
        "key1": {
            "slug": "legacy",
            "title": "Legacy",
            "md": "md/legacy-key1.md",
            "pdf": "pdfs/legacy-key1.pdf",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }
    }
    index_path(tmp_path).write_text(json.dumps(index), encoding="utf-8")

    summary = migrate_library(tmp_path)
    assert summary["migrated"] == 1
    final = bundle_paths(tmp_path, "key1")
    assert final.markdown.is_file()
    assert final.pdf.is_file()
    updated = load_index(index_path(tmp_path))
    assert updated["key1"]["md"] == "key1/paper.md"


class TestFtsQuoting:
    """FTS5 reads bare punctuation as syntax, which breaks research vocabulary."""

    def test_hyphenated_terms_are_quoted(self):
        from paperfetch_app.db import fts_query

        # Unquoted, "zero-shot" parses as column "zero" minus token "shot".
        assert fts_query("zero-shot") == '"zero-shot"'
        assert fts_query("few-shot anomaly") == '"few-shot" "anomaly"'

    def test_operators_are_honoured_but_terms_are_still_quoted(self):
        """Passing the whole string through was how `NOT rnn-based` crashed."""
        from paperfetch_app.db import fts_query

        assert fts_query("anomaly AND detection") == '"anomaly" AND "detection"'
        assert fts_query("attention NOT rnn-based") == '"attention" NOT "rnn-based"'

    @pytest.mark.parametrize(
        "query",
        ["zero-shot", "attention NOT rnn-based", 'he said "unbalanced', "a*b", "NEAR stuff", "trailing-"],
    )
    def test_no_query_reaches_sqlite_unescaped(self, query):
        """Every one of these raised OperationalError before quoting."""
        import sqlite3

        from paperfetch_app.db import fts_query

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(body, tokenize='unicode61')")
        conn.execute("INSERT INTO t VALUES ('zero-shot anomaly detection')")
        try:
            conn.execute("SELECT * FROM t WHERE t MATCH ?", (fts_query(query),)).fetchall()
        finally:
            conn.close()

    def test_empty_query_does_not_produce_invalid_sql(self):
        from paperfetch_app.db import fts_query

        assert fts_query("   ") == '""'

    def test_hyphenated_search_does_not_raise(self, tmp_path):
        from paperfetch_app import service

        _make_bundle(tmp_path)
        # Would raise OperationalError("no such column: detection") before quoting.
        service.grep(tmp_path, "anomaly-detection")
        assert service.grep(tmp_path, "anomaly")
