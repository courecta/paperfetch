from __future__ import annotations

from pathlib import Path

import pytest
import requests

from paperfetch_app.config import get_user_agent
from paperfetch_app.errors import DownloadError, InvalidPdfError, UnsafeUrlError
from paperfetch_app.fetch.http import HttpClient, TokenBucket


class FakeResponse:
    def __init__(self, status_code: int, body: bytes = b"", headers: dict[str, str] | None = None, url: str = "https://example.com/x"):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.calls = 0

    def iter_content(self, chunk_size: int = 1024):
        yield self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self):
        pass

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")


def test_token_bucket_rate_limit():
    bucket = TokenBucket(rate_per_sec=1000, burst=2)
    bucket.acquire()
    bucket.acquire()


def test_retries_on_503_then_succeeds(monkeypatch, tmp_path: Path):
    client = HttpClient(retries=2, backoff=0)
    responses = [FakeResponse(503), FakeResponse(200, body=b"ok")]

    def fake_request(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(client.session, "request", fake_request)
    response = client.request("GET", "https://example.com/x")
    assert response.status_code == 200


def test_no_retry_on_404(monkeypatch):
    client = HttpClient(retries=3, backoff=0)
    calls = {"n": 0}

    def fake_request(*args, **kwargs):
        calls["n"] += 1
        return FakeResponse(404, body=b"nope")

    monkeypatch.setattr(client.session, "request", fake_request)
    response = client.request("GET", "https://example.com/x")
    assert response.status_code == 404
    assert calls["n"] == 1


def test_retry_after_is_honored(monkeypatch, tmp_path: Path):
    client = HttpClient(retries=1, backoff=0)
    sleeps: list[float] = []
    responses = [FakeResponse(429, headers={"Retry-After": "0.01"}), FakeResponse(200, body=b"ok")]

    monkeypatch.setattr(client.session, "request", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr("paperfetch_app.fetch.http.time.sleep", lambda value: sleeps.append(value))
    response = client.request("GET", "https://example.com/x")
    assert response.status_code == 200
    assert sleeps and sleeps[0] == pytest.approx(0.01)


def test_download_rejects_html(monkeypatch, tmp_path: Path):
    client = HttpClient(retries=0)
    html = FakeResponse(200, body=b"<html>paywall</html>", headers={"Content-Type": "text/html"})

    def fake_request(method, url, **kwargs):
        return html

    monkeypatch.setattr(client.session, "request", fake_request)
    with pytest.raises(InvalidPdfError):
        client.download("https://example.com/paper.pdf", tmp_path / "paper.pdf")


def test_download_accepts_pdf(monkeypatch, tmp_path: Path):
    client = HttpClient(retries=0)
    body = b"%PDF-1.7 fake pdf content"
    response = FakeResponse(200, body=body, headers={"Content-Type": "application/pdf"})
    monkeypatch.setattr(client.session, "request", lambda *a, **k: response)
    result = client.download("https://example.com/paper.pdf", tmp_path / "paper.pdf")
    assert result.path.read_bytes() == body
    assert result.sha256


def test_download_exceeds_max_bytes(monkeypatch, tmp_path: Path):
    client = HttpClient(retries=0)
    body = b"%PDF-" + b"x" * 100
    response = FakeResponse(200, body=body)
    monkeypatch.setattr(client.session, "request", lambda *a, **k: response)
    with pytest.raises(DownloadError):
        client.download("https://example.com/paper.pdf", tmp_path / "paper.pdf", max_bytes=10)


def test_unsafe_scheme_rejected():
    client = HttpClient(retries=0)
    with pytest.raises(UnsafeUrlError):
        client.assert_safe_url("file:///etc/passwd")


def test_private_address_rejected(monkeypatch):
    client = HttpClient(retries=0)
    with pytest.raises(UnsafeUrlError):
        client.assert_safe_url("http://127.0.0.1:8080/paper.pdf")


def test_user_agent_contains_contact_placeholder(monkeypatch):
    monkeypatch.delenv("PAPERFETCH_MAILTO", raising=False)
    agent = get_user_agent()
    assert "paperfetch/" in agent


class TestSemanticScholarAuth:
    def test_key_is_sent_to_semantic_scholar(self, monkeypatch):
        from paperfetch_app.fetch.http import _auth_headers

        monkeypatch.setenv("PAPERFETCH_S2_API_KEY", "secret-key")
        assert _auth_headers("api.semanticscholar.org") == {"x-api-key": "secret-key"}

    def test_key_is_never_sent_to_other_hosts(self, monkeypatch):
        """A session-wide header would leak the credential to every publisher."""
        from paperfetch_app.fetch.http import _auth_headers

        monkeypatch.setenv("PAPERFETCH_S2_API_KEY", "secret-key")
        for host in ("arxiv.org", "export.arxiv.org", "openreview.net", "evil.example.com"):
            assert _auth_headers(host) == {}

    def test_lookalike_host_is_not_trusted(self, monkeypatch):
        from paperfetch_app.fetch.http import _auth_headers

        monkeypatch.setenv("PAPERFETCH_S2_API_KEY", "secret-key")
        assert _auth_headers("api.semanticscholar.org.evil.com") == {}

    def test_no_header_without_a_key(self, monkeypatch):
        from paperfetch_app.fetch.http import _auth_headers

        monkeypatch.delenv("PAPERFETCH_S2_API_KEY", raising=False)
        monkeypatch.delenv("S2_API_KEY", raising=False)
        assert _auth_headers("api.semanticscholar.org") == {}


class TestRedirectPolicy:
    """Each hop re-decides credentials and throttling for its own host."""

    def _redirecting_client(self, monkeypatch, hops):
        from paperfetch_app.fetch.http import HttpClient

        client = HttpClient(retries=0)
        # These hosts are fictional; the SSRF guard's DNS check is not what
        # these tests are about.
        monkeypatch.setattr(client, "assert_safe_url", lambda url: None)
        seen = []

        def fake_request(method, url, **kwargs):
            seen.append((url, dict(kwargs.get("headers") or {})))
            if hops:
                target = hops.pop(0)
                return FakeResponse(302, headers={"Location": target}, url=url)
            return FakeResponse(200, body=b"ok", url=url)

        monkeypatch.setattr(client.session, "request", fake_request)
        return client, seen

    def test_api_key_is_not_carried_to_the_redirect_target(self, monkeypatch):
        """requests strips Authorization across hosts, but not x-api-key."""
        monkeypatch.setenv("PAPERFETCH_S2_API_KEY", "secret-key")
        client, seen = self._redirecting_client(monkeypatch, ["https://cdn.elsewhere.example/p"])
        client.get_text("https://api.semanticscholar.org/graph/v1/paper/x")

        first_url, first_headers = seen[0]
        last_url, last_headers = seen[-1]
        assert first_headers.get("x-api-key") == "secret-key"
        assert "cdn.elsewhere.example" in last_url
        assert "x-api-key" not in last_headers

    def test_redirect_target_gets_its_own_rate_limit_bucket(self, monkeypatch):
        """A DOI redirecting to a publisher must be paced against the publisher."""
        client, _ = self._redirecting_client(monkeypatch, ["https://publisher.example/article.pdf"])
        client.get_text("https://doi.org/10.1/x")
        assert "publisher.example" in client._buckets
        assert "doi.org" in client._buckets

    def test_redirect_chains_are_bounded(self, monkeypatch):
        client, seen = self._redirecting_client(monkeypatch, [f"https://h{i}.example/x" for i in range(40)])
        client.get_text("https://start.example/x")
        assert len(seen) <= 12
