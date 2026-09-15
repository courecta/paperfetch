from __future__ import annotations

import base64
from pathlib import Path

from paperfetch_app.extract.marker_ir import document_from_marker_json

# 1x1 transparent PNG
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _payload() -> dict:
    return {
        "block_type": "Document",
        "children": [
            {
                "block_type": "Page",
                "id": "/page/0",
                "children": [
                    {"block_type": "PageHeader", "id": "/page/0/PageHeader/0", "html": "<p>running head</p>"},
                    {
                        "block_type": "SectionHeader",
                        "id": "/page/0/SectionHeader/1",
                        "html": "<h2>1 Introduction</h2>",
                    },
                    {
                        "block_type": "Text",
                        "id": "/page/0/Text/2",
                        "html": "<p>Anomaly detection matters.</p>",
                    },
                    {
                        "block_type": "FigureGroup",
                        "id": "/page/0/FigureGroup/3",
                        "children": [
                            {
                                "block_type": "Figure",
                                "id": "/page/0/Figure/3",
                                "images": {"/page/0/Figure/3": PNG_B64},
                            },
                            {
                                "block_type": "Caption",
                                "id": "/page/0/Caption/4",
                                "html": "<p>Figure 1: The model architecture.</p>",
                            },
                        ],
                    },
                    {
                        "block_type": "TableGroup",
                        "id": "/page/0/TableGroup/5",
                        "children": [
                            {
                                "block_type": "Table",
                                "id": "/page/0/Table/5",
                                "html": (
                                    "<table><thead><tr><th colspan='2'>Results</th></tr></thead>"
                                    "<tbody><tr><td rowspan='2'>A</td><td>1</td></tr>"
                                    "<tr><td>2</td></tr></tbody></table>"
                                ),
                            },
                            {
                                "block_type": "Caption",
                                "id": "/page/0/Caption/6",
                                "html": "<p>Table 1: Benchmark scores.</p>",
                            },
                        ],
                    },
                    {
                        "block_type": "Equation",
                        "id": "/page/0/Equation/7",
                        "html": "<p>E = mc^2</p>",
                    },
                ],
            }
        ],
    }


def _build(tmp_path: Path):
    return document_from_marker_json(
        _payload(),
        key="k",
        source_url="https://example.com/p.pdf",
        figures_dir=tmp_path / "figures",
        title="Test Paper",
    )


def test_marker_json_produces_structured_blocks(tmp_path: Path):
    doc = _build(tmp_path)
    kinds = [b.kind for b in doc.blocks]
    assert "heading" in kinds
    assert "paragraph" in kinds
    assert "figure" in kinds
    assert "table" in kinds
    assert "equation" in kinds
    # Page furniture is not document content.
    assert not any("running head" in (b.text or "") for b in doc.blocks)


def test_marker_figures_keep_captions_and_images(tmp_path: Path):
    doc = _build(tmp_path)
    figure = next(b.figure for b in doc.blocks if b.kind == "figure")
    assert figure.caption and "model architecture" in figure.caption[0].text
    assert figure.label == "Figure 1"
    assert len(figure.images) == 1
    written = tmp_path / "figures" / Path(figure.images[0].local).name
    assert written.is_file()
    assert written.read_bytes() == base64.b64decode(PNG_B64)
    assert figure.images[0].sha256


def test_marker_tables_preserve_spans(tmp_path: Path):
    doc = _build(tmp_path)
    table = next(b.table for b in doc.blocks if b.kind == "table")
    assert table.has_spans is True
    assert table.header_rows == 1
    assert table.rows[0][0].colspan == 2
    assert table.rows[1][0].rowspan == 2
    assert table.caption and "Benchmark scores" in table.caption[0].text
    assert "<table>" in table.html


def test_marker_coverage_is_accountable(tmp_path: Path):
    doc = _build(tmp_path)
    coverage = doc.coverage
    assert coverage["ir_available"] is True
    assert coverage["source_counts"]["figures"] == 1
    assert coverage["source_counts"]["tables"] == 1
    assert coverage["source_counts"]["math"] == 1
    assert coverage["mapped_counts"] == coverage["source_counts"]
    assert coverage["assets"] == {"images_total": 1, "images_downloaded": 1}


def test_marker_reports_unmapped_images(tmp_path: Path):
    payload = _payload()
    figure_group = payload["children"][0]["children"][3]
    figure_group["children"][0]["images"] = {"/page/0/Figure/3": "!!!not-base64!!!"}
    doc = document_from_marker_json(
        payload, key="k", source_url="u", figures_dir=tmp_path / "figures", title=""
    )
    assert doc.coverage["assets"]["images_downloaded"] == 0
    assert any(item["kind"] == "figure_images" for item in doc.coverage["unmapped"])
