from __future__ import annotations

import email.utils
import hashlib
import ipaddress
import random
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from ..config import (
    allow_private_urls,
    get_host_rps,
    get_s2_api_key,
    get_user_agent,
)
from ..errors import DownloadError, InvalidPdfError, NetworkError, UnsafeUrlError

RETRIABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRIABLE_EXCEPTIONS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
MAX_RETRY_AFTER = 60.0


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    url: str
    final_url: str
    sha256: str
    size: int
    content_type: str


class TokenBucket:
    """Simple thread-safe token bucket used for per-host politeness."""

    def __init__(self, rate_per_sec: float, burst: int = 1) -> None:
        self.rate = max(rate_per_sec, 0.01)
        self.burst = max(1, burst)
        self._tokens = float(self.burst)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            time.sleep(min(max(wait, 0.01), 5.0))


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
        if when is None:
            return None
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _host_is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


S2_API_HOST = "api.semanticscholar.org"


def _auth_headers(host: str) -> dict[str, str]:
    """Credentials for this host only.

    The key is attached per request rather than set on the session: a session
    header would be sent to every host the client touches -- arXiv, publishers,
    CDNs -- leaking the credential to third parties that have no business
    seeing it.
    """
    if host == S2_API_HOST or host.endswith("." + S2_API_HOST):
        api_key = get_s2_api_key()
        if api_key:
            return {"x-api-key": api_key}
    return {}


class HttpClient:
    """HTTP client with retries, per-host rate limiting, and content validation."""

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 1.0,
        session: requests.Session | None = None,
        user_agent: str | None = None,
        rps: float | None = None,
        verify: bool = True,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.rps = rps
        self.verify = verify
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent or get_user_agent())
        self.session.headers.setdefault("Accept-Encoding", "gzip, deflate")
        self._buckets: dict[str, TokenBucket] = {}
        self._buckets_lock = threading.Lock()
        self._robots: dict[str, RobotFileParser | None] = {}
        self._safe_hosts: set[str] = set()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _bucket_for(self, host: str) -> TokenBucket:
        with self._buckets_lock:
            bucket = self._buckets.get(host)
            if bucket is None:
                bucket = TokenBucket(self.rps if self.rps is not None else get_host_rps(host))
                self._buckets[host] = bucket
            return bucket

    def assert_safe_url(self, url: str) -> None:
        """Reject non-http(s) schemes and hosts resolving to private addresses."""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeUrlError(f"Unsupported URL scheme: {parsed.scheme or '(none)'}")
        host = parsed.hostname
        if not host:
            raise UnsafeUrlError(f"URL has no host: {url}")
        if allow_private_urls():
            return
        if host in self._safe_hosts:
            return
        if _host_is_ip(host):
            addresses = [host]
        else:
            try:
                infos = socket.getaddrinfo(host, None)
            except socket.gaierror as exc:
                raise NetworkError(f"Cannot resolve host {host}: {exc}") from exc
            addresses = sorted({info[4][0] for info in infos})
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise UnsafeUrlError(
                    f"Refusing to fetch private address {address} for host {host}",
                    hint="Set PAPERFETCH_ALLOW_PRIVATE_URLS=1 to override for intranet sources.",
                )
        self._safe_hosts.add(host)

    def robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        key = f"{parsed.scheme}://{parsed.netloc}"
        if key in self._robots:
            parser = self._robots[key]
            if parser is None:
                return True
            return parser.can_fetch("*", url)

        parser: RobotFileParser | None = RobotFileParser()
        try:
            resp = self.session.get(
                f"{key}/robots.txt",
                timeout=min(self.timeout, 15),
                headers={"Accept": "text/plain"},
            )
            if resp.status_code >= 400:
                parser = None
            else:
                parser.parse(resp.text.splitlines())
        except requests.RequestException:
            parser = None
        self._robots[key] = parser
        if parser is None:
            return True
        return parser.can_fetch("*", url)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        stream: bool = False,
        allow_redirects: bool = True,
        timeout: float | None = None,
        retries: int | None = None,
        backoff: float | None = None,
        check_url: bool = False,
    ) -> requests.Response:
        if check_url:
            self.assert_safe_url(url)
        host = urlparse(url).hostname or ""
        attempts = self.retries if retries is None else max(0, retries)
        base_backoff = self.backoff if backoff is None else max(0.0, backoff)
        timeout_val = timeout if timeout is not None else self.timeout
        last_error: Exception | None = None

        request_headers = dict(headers) if headers else {}
        request_headers.update(_auth_headers(host))

        for attempt in range(attempts + 1):
            self._bucket_for(host).acquire()
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=request_headers or None,
                    params=dict(params) if params else None,
                    stream=stream,
                    allow_redirects=allow_redirects,
                    timeout=timeout_val,
                )
            except RETRIABLE_EXCEPTIONS as exc:
                last_error = exc
                if attempt >= attempts:
                    raise NetworkError(f"{method} {url} failed after {attempt + 1} attempts: {exc}") from exc
                self._sleep_backoff(base_backoff, attempt)
                continue

            if response.status_code in RETRIABLE_STATUS and attempt < attempts:
                delay = _parse_retry_after(response.headers.get("Retry-After"))
                response.close()
                if delay is None:
                    self._sleep_backoff(base_backoff, attempt)
                else:
                    time.sleep(min(delay, MAX_RETRY_AFTER))
                continue

            if response.status_code in RETRIABLE_STATUS and attempt >= attempts:
                status = response.status_code
                response.close()
                raise DownloadError(
                    f"{method} {url} failed with HTTP {status} after {attempt + 1} attempts",
                    url=url,
                    status_code=status,
                )
            return response

        raise NetworkError(f"{method} {url} failed: {last_error}")

    @staticmethod
    def _sleep_backoff(base: float, attempt: int) -> None:
        if base <= 0:
            return
        window = base * (2**attempt)
        time.sleep(random.uniform(0.0, min(window, 30.0)))

    @staticmethod
    def _check_status(response: requests.Response, url: str) -> None:
        """Raise a DownloadError, not requests.HTTPError.

        Non-retriable statuses (404/403/401) come back as a normal response, so
        raise_for_status would raise requests.HTTPError -- outside the
        PaperfetchError hierarchy, which made every `except PaperfetchError`
        guard in the resolvers dead code: one 404 from Crossref aborted the
        whole DOI resolution chain instead of falling through to OpenAlex.
        """
        if response.status_code >= 400:
            raise DownloadError(f"GET {url} failed with HTTP {response.status_code}")

    def get_text(self, url: str, **kwargs: Any) -> str:
        response = self.request("GET", url, **kwargs)
        try:
            self._check_status(response, url)
            return response.text
        finally:
            response.close()

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.request("GET", url, **kwargs)
        try:
            self._check_status(response, url)
            try:
                return response.json()
            except ValueError as exc:
                # An HTML interstitial served with HTTP 200 is common.
                raise DownloadError(f"GET {url} returned a non-JSON body: {exc}") from exc
        finally:
            response.close()

    def get_bytes(self, url: str, *, limit: int | None = None, **kwargs: Any) -> bytes:
        response = self.request("GET", url, **kwargs)
        try:
            self._check_status(response, url)
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if limit is not None and total > limit:
                    raise DownloadError(f"Response exceeded limit of {limit} bytes", url=url)
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()

    def download(
        self,
        url: str,
        out_path: Path,
        *,
        expect_pdf: bool = True,
        max_bytes: int | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        backoff: float | None = None,
        check_url: bool = False,
    ) -> DownloadResult:
        """Stream a URL to disk, validating that the payload looks like a PDF."""
        response = self.request(
            "GET",
            url,
            headers=headers,
            stream=True,
            timeout=timeout,
            retries=retries,
            backoff=backoff,
            check_url=check_url,
        )
        try:
            if response.status_code >= 400:
                raise DownloadError(
                    f"GET {url} failed with HTTP {response.status_code}",
                    url=url,
                    status_code=response.status_code,
                )

            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_suffix(out_path.suffix + ".part")
            digest = hashlib.sha256()
            total = 0
            first = b""
            try:
                with tmp_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        if not first:
                            first = chunk[:5]
                        total += len(chunk)
                        if max_bytes is not None and total > max_bytes:
                            raise DownloadError(
                                f"Download exceeded {max_bytes} bytes",
                                url=url,
                                hint="Increase PAPERFETCH_MAX_PDF_MB if this is a legitimate large PDF.",
                            )
                        digest.update(chunk)
                        handle.write(chunk)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

            if expect_pdf and first != b"%PDF-":
                tmp_path.unlink(missing_ok=True)
                raise InvalidPdfError(
                    f"Downloaded content from {response.url} is not a PDF (Content-Type: {content_type or 'unknown'})",
                    url=str(response.url),
                    content_type=content_type,
                )

            tmp_path.replace(out_path)
            return DownloadResult(
                path=out_path,
                url=url,
                final_url=str(response.url),
                sha256=digest.hexdigest(),
                size=total,
                content_type=content_type,
            )
        finally:
            response.close()


def default_client(**kwargs: Any) -> HttpClient:
    """Deprecated alias; HttpClient authenticates Semantic Scholar by itself."""
    return HttpClient(**kwargs)
