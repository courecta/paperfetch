from __future__ import annotations

from typing import Any


class PaperfetchError(Exception):
    """Base class for all paperfetch errors."""

    code = "paperfetch_error"
    hint: str | None = None

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        if hint:
            self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.hint:
            payload["hint"] = self.hint
        return payload


class NetworkError(PaperfetchError):
    code = "network_error"


class DownloadError(NetworkError):
    code = "download_error"

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.url = url
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.url:
            payload["url"] = self.url
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        return payload


class InvalidPdfError(DownloadError):
    code = "invalid_pdf"

    def __init__(self, message: str = "Downloaded content is not a PDF", *, url: str | None = None, content_type: str | None = None) -> None:
        super().__init__(message, url=url, hint="The source likely returned an HTML landing page or a paywall.")
        self.content_type = content_type


class UnsafeUrlError(PaperfetchError):
    code = "unsafe_url"


class UnsupportedSourceError(PaperfetchError):
    code = "unsupported_source"


class PaywalledError(PaperfetchError):
    code = "paywalled"

    def __init__(self, message: str, *, url: str | None = None, alternatives: list[str] | None = None) -> None:
        super().__init__(message, hint="No open-access copy was found. Provide a local PDF with --pdf if you have access.")
        self.url = url
        self.alternatives = alternatives or []


class ExtractionError(PaperfetchError):
    code = "extraction_error"


class MarkerUnavailableError(ExtractionError):
    code = "marker_unavailable"


class CoverageError(ExtractionError):
    code = "coverage_error"

    def __init__(self, message: str, *, report: dict[str, Any] | None = None) -> None:
        super().__init__(message, hint="Re-run with --allow-incomplete to accept partial extraction.")
        self.report = report or {}


class QualityError(ExtractionError):
    code = "quality_error"


class NotFoundError(PaperfetchError):
    code = "not_found"
