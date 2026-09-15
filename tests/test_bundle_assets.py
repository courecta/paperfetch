from __future__ import annotations

from pathlib import Path

from paperfetch_app.assets import render_table_html, table_to_csv, write_table_sidecars
from paperfetch_app.bundle import bundle_paths, promote, staging_bundle, write_checksums
from paperfetch_app.extract.ir import Block, Cell, Document, Inline, Table


def _span_table() -> Block:
    table = Table(
        id="S4.T1",
        label="Table 1",
        caption=[Inline(kind="text", text="Results")],
        header_rows=1,
        rows=[
            [Cell(inlines=[Inline(kind="text", text="Name")], header=True), Cell(inlines=[Inline(kind="text", text="Value")], header=True)],
            [Cell(inlines=[Inline(kind="text", text="Temperature")], rowspan=2), Cell(inlines=[Inline(kind="text", text="353.15")])],
            [Cell(inlines=[Inline(kind="text", text="353.20")])],
        ],
        has_spans=True,
    )
    return Block(kind="table", id="S4.T1", table=table)


def test_table_to_csv_expands_rowspan():
    table = _span_table().table
    csv_text = table_to_csv(table.to_dict())
    lines = [line for line in csv_text.splitlines() if line.strip()]
    assert lines[0] == "Name,Value"
    assert lines[1].startswith("Temperature,353.15")
    assert lines[2].startswith("Temperature,353.20")


def test_render_table_html_preserves_spans():
    html = render_table_html(_span_table().table.to_dict())
    assert 'rowspan="2"' in html
    assert "Temperature" in html


def test_write_table_sidecars(tmp_path: Path):
    document = Document(schema_version=1, key="k", source_kind="arxiv_html", source_url="")
    document.blocks.append(_span_table())
    paths = bundle_paths(tmp_path, "k")
    paths.root.mkdir(parents=True)
    write_table_sidecars(document, paths)
    assert (paths.tables_dir / "s4.t1.html").is_file()
    assert (paths.tables_dir / "s4.t1.csv").is_file()
    assert paths.tables_manifest().is_file()


def test_staging_promote_and_checksums(tmp_path: Path):
    final = bundle_paths(tmp_path, "key1")
    with staging_bundle(tmp_path, "key1") as staging:
        (staging.root / "paper.md").write_text("# Test\n")
        staging.meta.write_text('{"key": "key1"}\n')
        checksums = write_checksums(staging)
        promote(staging.root, final.root)
    assert final.meta.is_file()
    assert "paper.md" in checksums
    assert (final.root / "paper.md").read_text() == "# Test\n"


def test_promote_backup_on_failure(tmp_path: Path):
    final = bundle_paths(tmp_path, "key1")
    final.root.mkdir(parents=True)
    (final.root / "meta.json").write_text('{"old": true}\n')
    assert final.meta.is_file()
    assert (tmp_path / "key1" / "meta.json").read_text() == '{"old": true}\n'
