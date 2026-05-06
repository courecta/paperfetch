from __future__ import annotations

import os
from pathlib import Path


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
