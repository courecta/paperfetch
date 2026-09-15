from __future__ import annotations

import pytest

from paperfetch_app.extract.coverage import audit
from paperfetch_app.extract.html_arxiv import LatexmlConverter
from paperfetch_app.extract.render_markdown import render_markdown

TABLE_FIXTURE = """
<html><body><article class="ltx_document">
<h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Results</h2>
<figure class="ltx_table" id="S4.T1">
  <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_table">Table 1</span>:
    <span>Operating conditions reported by <cite class="ltx_cite">[<a class="ltx_ref" href="#bib.bib36">36</a>]</cite>.</span>
  </figcaption>
  <table class="ltx_tabular ltx_centering" id="S4.T1.5">
    <thead><tr>
      <th class="ltx_th"><span>Name</span></th>
      <th class="ltx_th"><span>Value</span></th>
    </tr></thead>
    <tbody>
      <tr><th class="ltx_th">Temperature</th><td><math alttext="T"><annotation encoding="application/x-tex">T</annotation></math></td></tr>
      <tr><td colspan="2">merged cell</td></tr>
    </tbody>
  </table>
</figure>
</article></body></html>
"""

EQUATION_FIXTURE = """
<html><body><article class="ltx_document">
<table class="ltx_equationgroup ltx_eqn_align ltx_eqn_table" id="S2.EGx1">
<tbody id="S2.E1"><tr class="ltx_equation ltx_eqn_row">
  <td class="ltx_eqn_cell ltx_eqn_left_padleft"></td>
  <td class="ltx_eqn_cell"><math alttext="a=b" display="inline"><annotation encoding="application/x-tex">a=b</annotation></math></td>
  <td class="ltx_eqn_cell ltx_eqn_eqno"><span class="ltx_tag ltx_tag_equation">(1)</span></td>
</tr></tbody>
<tbody id="S2.E2"><tr class="ltx_equation ltx_eqn_row">
  <td class="ltx_eqn_cell"><math alttext="c=d" display="inline"><annotation encoding="application/x-tex">c=d</annotation></math></td>
  <td class="ltx_eqn_cell ltx_eqn_eqno"><span class="ltx_tag ltx_tag_equation">(2)</span></td>
</tr></tbody>
</table>
<table class="ltx_equation ltx_eqn_table" id="S2.Ex1">
<tbody><tr class="ltx_equation ltx_eqn_row">
  <td class="ltx_eqn_cell ltx_align_left"><math display="block" alttext="e=f"><annotation encoding="application/x-tex">e=f</annotation></math></td>
  <td class="ltx_eqn_cell ltx_eqn_eqno"><span class="ltx_tag ltx_tag_equation">(3)</span></td>
</tr></tbody>
</table>
</article></body></html>
"""

FIGURE_FIXTURE = """
<html><body><article class="ltx_document">
<figure class="ltx_figure" id="S4.F1">
  <div class="ltx_flex_figure">
    <span class="ltx_picture"><img src="2501.00001v1/Figures/a.png" alt="panel a"/></span>
    <span class="ltx_picture"><img srcset="2501.00001v1/Figures/b-small.png 1x, 2501.00001v1/Figures/b.png 2x" alt="panel b"/></span>
  </div>
  <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 1</span>:
    Results for <math alttext="\\hat{t}=70"><annotation encoding="application/x-tex">\\hat{t}=70</annotation></math>.</figcaption>
</figure>
<figure class="ltx_figure" id="S4.F2">
  <div><img src="p2.png"/></div>
  <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 2</span>: Second.</figcaption>
</figure>
</article></body></html>
"""


def _convert(html: str) -> object:
    converter = LatexmlConverter(
        key="test",
        source_url="https://arxiv.org/html/2501.00001",
        base_url="https://arxiv.org/html/2501.00001",
    )
    return converter.convert(html)


class TestTableConversion:
    def test_table_body_is_preserved(self):
        document = _convert(TABLE_FIXTURE)
        tables = [block for block in document.blocks if block.kind == "table"]
        assert len(tables) == 1
        table = tables[0].table
        assert table.id == "S4.T1"
        assert table.label == "Table 1"
        assert table.header_rows == 1
        assert table.has_spans is True
        assert table.rows[1][0].inlines[0].text == "Temperature"

    def test_caption_and_math_capture(self):
        document = _convert(TABLE_FIXTURE)
        table = next(block.table for block in document.blocks if block.kind == "table")
        assert table.rows[1][1].inlines[0].kind == "math"
        rendered = render_markdown(document, include_frontmatter=False)
        assert "Temperature" in rendered
        assert "merged cell" in rendered
        assert "Table 1" in rendered

    def test_coverage_is_complete(self):
        document = _convert(TABLE_FIXTURE)
        report = audit(document)
        assert report["ok"], report
        assert report["source_counts"]["tables"] == 1


class TestEquationConversion:
    def test_equations_are_display_math_not_tables(self):
        document = _convert(EQUATION_FIXTURE)
        equations = [block for block in document.blocks if block.kind == "math"]
        tables = [block for block in document.blocks if block.kind == "table"]
        assert tables == []
        assert [block.meta.get("label") for block in equations] == ["(1)", "(2)", "(3)"]
        assert equations[0].tex == "a=b"
        assert equations[2].tex == "e=f"

    def test_rendered_with_tags(self):
        document = _convert(EQUATION_FIXTURE)
        rendered = render_markdown(document, include_frontmatter=False)
        assert "\\tag{1}" in rendered
        assert "| ---" not in rendered


class TestFigureConversion:
    def test_all_panels_captured(self):
        document = _convert(FIGURE_FIXTURE)
        figures = [block for block in document.blocks if block.kind == "figure"]
        assert len(figures) == 2
        first = figures[0].figure
        assert first.label == "Figure 1"
        assert len(first.images) == 2
        assert first.images[0].src == "https://arxiv.org/html/2501.00001v1/Figures/a.png"
        assert first.images[1].src.endswith("Figures/b.png")
        assert "Results for" in render_markdown(document, include_frontmatter=False)

    def test_coverage_counts_figures(self):
        document = _convert(FIGURE_FIXTURE)
        report = audit(document)
        assert report["source_counts"]["figures"] == 2
        assert report["mapped_counts"]["figures"] == 2


def test_markdown_render_has_frontmatter():
    document = _convert(TABLE_FIXTURE)
    rendered = render_markdown(document, metadata={"authors": ["A"], "year": 2025})
    assert rendered.startswith("---")
    assert "paperfetch_key: test" in rendered


@pytest.mark.network
class TestArxivHtmlIntegration:
    def test_fetch_real_arxiv_html(self):
        from paperfetch_app.extract.html_arxiv import extract_arxiv_document

        document = extract_arxiv_document("2501.00001", key="k", source_url="https://arxiv.org/html/2501.00001")
        assert len(document.blocks) > 100
        assert any(block.kind == "table" for block in document.blocks)
        assert any(block.kind == "figure" for block in document.blocks)
        assert any(block.kind == "math" for block in document.blocks)
