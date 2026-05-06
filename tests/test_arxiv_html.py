from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from paperfetch_app.arxiv_html import (
    _convert_element,
    _convert_table,
    extract_markdown_from_html,
)


class TestConvertElement:
    def test_paragraph(self):
        soup = BeautifulSoup("<p>Hello world</p>", "html.parser")
        result = _convert_element(soup.p)
        assert "Hello world" in result

    def test_heading(self):
        soup = BeautifulSoup("<h2>Section Title</h2>", "html.parser")
        result = _convert_element(soup.h2)
        assert "## Section Title" in result

    def test_bold(self):
        soup = BeautifulSoup("<b>bold text</b>", "html.parser")
        result = _convert_element(soup.b)
        assert "**bold text**" in result

    def test_italic(self):
        soup = BeautifulSoup("<em>italic text</em>", "html.parser")
        result = _convert_element(soup.em)
        assert "*italic text*" in result

    def test_link(self):
        soup = BeautifulSoup('<a href="https://example.com">link</a>', "html.parser")
        result = _convert_element(soup.a)
        assert "[link](https://example.com)" in result

    def test_code(self):
        soup = BeautifulSoup("<code>print('hi')</code>", "html.parser")
        result = _convert_element(soup.code)
        assert "`print('hi')`" in result

    def test_pre(self):
        soup = BeautifulSoup("<pre>code block</pre>", "html.parser")
        result = _convert_element(soup.pre)
        assert "```" in result
        assert "code block" in result

    def test_list(self):
        html = "<ul><li>item 1</li><li>item 2</li></ul>"
        soup = BeautifulSoup(html, "html.parser")
        result = _convert_element(soup.ul)
        assert "- item 1" in result
        assert "- item 2" in result

    def test_math_inline(self):
        html = '<p><math><mi>x</mi><mo>=</mo><mn>1</mn></math></p>'
        soup = BeautifulSoup(html, "html.parser")
        result = _convert_element(soup.p)
        assert "$" in result

    def test_math_display(self):
        html = '<div><math display="block"><mi>x</mi></math></div>'
        soup = BeautifulSoup(html, "html.parser")
        result = _convert_element(soup.div)
        assert "\\[" in result

    def test_script_removed(self):
        html = '<div><script>alert("x")</script>text</div>'
        soup = BeautifulSoup(html, "html.parser")
        result = _convert_element(soup.div)
        assert "alert" not in result
        assert "text" in result


class TestConvertTable:
    def test_simple_table(self):
        html = """
        <table>
            <tr><th>A</th><th>B</th></tr>
            <tr><td>1</td><td>2</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = _convert_table(soup.table)
        assert "| A | B |" in result
        assert "| 1 | 2 |" in result
        assert "---" in result

    def test_no_header(self):
        html = "<table><tr><td>a</td><td>b</td></tr></table>"
        soup = BeautifulSoup(html, "html.parser")
        result = _convert_table(soup.table)
        assert "| a | b |" in result

    def test_empty_table(self):
        html = "<table></table>"
        soup = BeautifulSoup(html, "html.parser")
        result = _convert_table(soup.table)
        assert result == ""


class TestExtractMarkdownFromHtml:
    def test_basic_document(self):
        html = """
        <html><body>
        <h1>Title</h1>
        <p>Paragraph 1</p>
        <p>Paragraph 2</p>
        </body></html>
        """
        result = extract_markdown_from_html(html)
        assert "# Title" in result
        assert "Paragraph 1" in result
        assert "Paragraph 2" in result

    def test_removes_scripts(self):
        html = """
        <html><body>
        <script>alert('x')</script>
        <p>Content</p>
        </body></html>
        """
        result = extract_markdown_from_html(html)
        assert "alert" not in result
        assert "Content" in result

    def test_paper_id_comment(self):
        html = "<html><body><p>Text</p></body></html>"
        result = extract_markdown_from_html(html, paper_id="2401.00001")
        assert "<!-- arXiv:2401.00001 -->" in result

    def test_collapses_whitespace(self):
        html = "<html><body><p>A</p><p>B</p></body></html>"
        result = extract_markdown_from_html(html)
        # Should not have excessive blank lines
        assert "\n\n\n\n" not in result

    def test_empty_document(self):
        html = "<html><body></body></html>"
        result = extract_markdown_from_html(html)
        assert result.strip() == ""

    def test_unescapes_html_entities(self):
        html = "<html><body><p>A &amp; B</p></body></html>"
        result = extract_markdown_from_html(html)
        assert "A & B" in result


class TestArxivHtmlIntegration:
    """Integration tests that hit real arXiv HTML (if available)."""

    def test_fetch_real_arxiv_html(self):
        """Fetch a known arXiv paper with HTML."""
        from paperfetch_app.arxiv_html import extract_arxiv_markdown

        try:
            md = extract_arxiv_markdown("2501.00001")
        except RuntimeError as exc:
            if "404" in str(exc):
                pytest.skip("arXiv HTML not available for this paper")
            raise

        assert len(md) > 1000
        assert "#" in md  # Has headings

    def test_fetch_metadata_for_real_paper(self):
        """Fetch metadata for a known arXiv paper."""
        from paperfetch_app.metadata import fetch_arxiv_metadata

        meta = fetch_arxiv_metadata("2501.00001")
        assert meta.title
        assert len(meta.authors) > 0
        assert meta.year is not None
        assert meta.bibtex is not None
        assert "@misc{" in meta.bibtex
