from __future__ import annotations

import os
from pathlib import Path

VERSION = "0.4.0"

DEFAULT_RPS = 2.0
HOST_RPS: dict[str, float] = {
    "export.arxiv.org": 1.0 / 3.0,
    "arxiv.org": 1.0,
    "api.semanticscholar.org": 1.0,
    "api.openalex.org": 5.0,
    "api.unpaywall.org": 5.0,
    "www.ncbi.nlm.nih.gov": 2.0,
    "eutils.ncbi.nlm.nih.gov": 2.0,
}


def _xdg_data_home() -> Path:
    if "XDG_DATA_HOME" in os.environ:
        return Path(os.environ["XDG_DATA_HOME"])
    return Path.home() / ".local" / "share"


def _xdg_cache_home() -> Path:
    if "XDG_CACHE_HOME" in os.environ:
        return Path(os.environ["XDG_CACHE_HOME"])
    return Path.home() / ".cache"


def get_default_library_dir() -> Path:
    """Return the default paper library directory.

    Resolution order:
    1. $PAPERFETCH_LIBRARY environment variable
    2. ~/papers/library (legacy / user-friendly default)
    """
    if "PAPERFETCH_LIBRARY" in os.environ:
        return Path(os.environ["PAPERFETCH_LIBRARY"]).expanduser().resolve()
    return Path.home() / "papers" / "library"


def get_default_cache_dir() -> Path:
    """Return the default cache directory for paperfetch.

    Resolution order:
    1. $PAPERFETCH_CACHE environment variable
    2. XDG_CACHE_HOME/paperfetch
    3. ~/.cache/paperfetch
    """
    if "PAPERFETCH_CACHE" in os.environ:
        return Path(os.environ["PAPERFETCH_CACHE"]).expanduser().resolve()
    return _xdg_cache_home() / "paperfetch"


def get_default_marker_venv() -> Path:
    """Return the default marker virtualenv path."""
    return get_default_cache_dir() / "marker_venv"


def get_venv_bin_dir(venv_path: Path) -> Path:
    """Return the bin directory for a venv, cross-platform."""
    if os.name == "nt":
        return venv_path / "Scripts"
    return venv_path / "bin"


def get_mailto() -> str | None:
    """Return the contact address used for polite API pools."""
    value = os.environ.get("PAPERFETCH_MAILTO") or os.environ.get("OPENALEX_MAILTO")
    return value.strip() or None if value else None


def get_s2_api_key() -> str | None:
    value = os.environ.get("PAPERFETCH_S2_API_KEY") or os.environ.get("S2_API_KEY")
    return value.strip() or None if value else None


def get_user_agent() -> str:
    """Build a descriptive User-Agent, including contact info when configured."""
    explicit = os.environ.get("PAPERFETCH_USER_AGENT")
    if explicit and explicit.strip():
        return explicit.strip()

    parts = [f"paperfetch/{VERSION}", "(+https://github.com/courecta/paperfetch)"]
    mailto = get_mailto()
    if mailto:
        parts.append(f"mailto:{mailto}")
    else:
        parts.append("mailto:unconfigured (set PAPERFETCH_MAILTO)")
    return " ".join(parts)


def get_default_rps() -> float:
    raw = os.environ.get("PAPERFETCH_RPS")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_RPS


def get_host_rps(host: str) -> float:
    base = get_default_rps()
    for suffix, rate in HOST_RPS.items():
        if host == suffix or host.endswith("." + suffix):
            return min(base, rate) if base else rate
    return base


def allow_private_urls() -> bool:
    return os.environ.get("PAPERFETCH_ALLOW_PRIVATE_URLS", "").strip().lower() in {"1", "true", "yes", "on"}


def get_library_db_path(library_dir: Path) -> Path:
    return library_dir / "library.sqlite3"
