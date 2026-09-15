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
