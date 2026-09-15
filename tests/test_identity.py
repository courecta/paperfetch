from __future__ import annotations

from paperfetch_app.identity import build_identity, normalize_url


class TestNormalizeUrl:
    def test_arxiv_abs(self):
        assert normalize_url("https://arxiv.org/abs/2411.00278") == "https://arxiv.org/pdf/2411.00278.pdf"

    def test_arxiv_pdf(self):
        assert normalize_url("https://arxiv.org/pdf/2411.00278.pdf") == "https://arxiv.org/pdf/2411.00278.pdf"

    def test_arxiv_abs_with_version(self):
        assert normalize_url("https://arxiv.org/abs/2411.00278v1") == "https://arxiv.org/pdf/2411.00278v1.pdf"

    def test_openreview(self):
        assert normalize_url("https://openreview.net/forum?id=abc123") == "https://openreview.net/pdf?id=abc123"

    def test_openreview_pdf(self):
        assert normalize_url("https://openreview.net/pdf?id=abc123") == "https://openreview.net/pdf?id=abc123"

    def test_passthrough(self):
        url = "https://example.com/paper.pdf"
        assert normalize_url(url) == url


class TestBuildIdentity:
    def test_arxiv_identity(self):
        from paperfetch_app.io_utils import sha256_text
        identity = build_identity("https://arxiv.org/abs/2411.00278")
        assert identity.kind == "arxiv"
        assert identity.value == "2411.00278"
        assert identity.normalized_url == "https://arxiv.org/pdf/2411.00278.pdf"
        expected_key = sha256_text("arxiv:2411.00278")
        assert identity.key == expected_key

    def test_openreview_identity(self):
        identity = build_identity("https://openreview.net/forum?id=abc123")
        assert identity.kind == "openreview"
        assert identity.value == "abc123"
        assert identity.normalized_url == "https://openreview.net/pdf?id=abc123"

    def test_doi_identity(self):
        identity = build_identity("https://doi.org/10.1000/xyz123")
        assert identity.kind == "doi"
        assert identity.value == "10.1000/xyz123"

    def test_generic_url_identity(self):
        identity = build_identity("https://example.com/paper.pdf")
        assert identity.kind == "url"
        assert identity.value == "https://example.com/paper.pdf"

    def test_consistency(self):
        """Same URL must produce same key."""
        i1 = build_identity("https://arxiv.org/abs/2411.00278")
        i2 = build_identity("https://arxiv.org/abs/2411.00278")
        assert i1.key == i2.key
        assert i1.fingerprint == i2.fingerprint

    def test_different_papers_different_keys(self):
        i1 = build_identity("https://arxiv.org/abs/2411.00278")
        i2 = build_identity("https://arxiv.org/abs/2411.00279")
        assert i1.key != i2.key

    def test_whitespace_handling(self):
        i1 = build_identity("  https://arxiv.org/abs/2411.00278  ")
        i2 = build_identity("https://arxiv.org/abs/2411.00278")
        assert i1.key == i2.key
