from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .config import get_default_marker_venv

DEFAULT_MAX_CHARS = 8000


def _fastmcp_class() -> Any | None:
    """Return the FastMCP/MCPServer class across MCP SDK v1 and v2."""
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP
    except Exception:
        pass
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except Exception:
        return None


def _sdk_available() -> bool:
    return _fastmcp_class() is not None


def _to_sdk_content(items: list[dict[str, Any]]) -> list[Any]:
    from mcp.types import ImageContent, TextContent

    out: list[Any] = []
    for item in items:
        if item.get("type") == "image":
            out.append(ImageContent(type="image", data=item["data"], mime_type=item.get("mimeType", "image/png")))
        else:
            out.append(TextContent(type="text", text=item.get("text", "")))
    return out


def build_fastmcp_server(
    library_dir: Path,
    marker_venv: Path,
    out_dir: Path | None = None,
    project_files: str = "none",
) -> Any:
    server_class = _fastmcp_class()
    if server_class is None:
        raise RuntimeError("mcp SDK is not installed")

    from .mcp_stdio import DEFAULT_MAX_CHARS as _DEFAULT
    from .mcp_stdio import _handle_tool, server_instructions

    # The SDK surfaces `instructions` in the initialize response, same as the
    # built-in server, so an agent is oriented before its first call.
    server = server_class("paperfetch", instructions=server_instructions(library_dir, out_dir, project_files))

    @server.tool()
    def paperfetch_search(query: str, limit: int = 10, year: str = "", open_access: bool = False) -> list[Any]:
        """Search academic papers via Semantic Scholar; returns titles, authors, years, and URLs."""
        args = {"query": query, "limit": limit, "open_access": open_access}
        if year:
            args["year"] = year
        return _to_sdk_content(_handle_tool(library_dir, marker_venv, "paperfetch_search", args))

    @server.tool()
    def paperfetch_fetch(
        urls: list[str],
        extractor: str = "auto",
        force_download: bool = False,
        refresh_md: bool = False,
        background: bool = True,
    ) -> list[Any]:
        """Ingest papers into the local library; figures/tables are preserved in the bundle.

        Runs in the background by default and returns a job id, because
        extraction takes minutes per paper and would otherwise outlast the
        client's tool-call timeout. Poll with paperfetch_jobs.
        """
        args = {
            "urls": urls,
            "extractor": extractor,
            "force_download": force_download,
            "refresh_md": refresh_md,
            "background": background,
        }
        return _to_sdk_content(
            _handle_tool(library_dir, marker_venv, "paperfetch_fetch", args, out_dir, project_files)
        )

    @server.tool()
    def paperfetch_jobs(job_id: str = "", limit: int = 10) -> list[Any]:
        """Check background ingestion jobs; omit job_id to list recent ones."""
        args: dict[str, Any] = {"limit": limit}
        if job_id:
            args["job_id"] = job_id
        return _to_sdk_content(_handle_tool(library_dir, marker_venv, "paperfetch_jobs", args))

    @server.tool()
    def paperfetch_outline(key: str) -> list[Any]:
        """List the section outline of a cached paper with character counts."""
        return _to_sdk_content(_handle_tool(library_dir, marker_venv, "paperfetch_outline", {"key": key}))

    @server.tool()
    def paperfetch_read(
        key: str = "",
        url: str = "",
        section: str = "",
        offset: int = 0,
        max_chars: int = _DEFAULT,
    ) -> list[Any]:
        """Read extracted markdown (optionally one section) with offset/next_offset pagination."""
        args: dict[str, Any] = {"key": key, "url": url, "offset": offset, "max_chars": max_chars}
        if section:
            args["section"] = section
        return _to_sdk_content(_handle_tool(library_dir, marker_venv, "paperfetch_read", args))

    @server.tool()
    def paperfetch_figure(key: str, figure: str) -> list[Any]:
        """Return a figure image plus its caption from a cached paper."""
        return _to_sdk_content(_handle_tool(library_dir, marker_venv, "paperfetch_figure", {"key": key, "figure": figure}))

    @server.tool()
    def paperfetch_table(key: str, table: str) -> list[Any]:
        """Return a table's canonical HTML (or CSV) plus caption from a cached paper."""
        return _to_sdk_content(_handle_tool(library_dir, marker_venv, "paperfetch_table", {"key": key, "table": table}))

    @server.tool()
    def paperfetch_grep(query: str, key: str = "", limit: int = 20) -> list[Any]:
        """Full-text search across cached papers using SQLite FTS5; returns matching snippets."""
        args: dict[str, Any] = {"query": query, "limit": limit}
        if key:
            args["key"] = key
        return _to_sdk_content(_handle_tool(library_dir, marker_venv, "paperfetch_grep", args))

    @server.tool()
    def paperfetch_annotate(key: str, quote: str, note: str = "") -> list[Any]:
        """Save a quote/note attached to a cached paper."""
        return _to_sdk_content(
            _handle_tool(library_dir, marker_venv, "paperfetch_annotate", {"key": key, "quote": quote, "note": note})
        )

    @server.tool()
    def paperfetch_export(keys: list[str] | None = None, all: bool = False) -> list[Any]:
        """Export library entries to BibTeX."""
        return _to_sdk_content(
            _handle_tool(library_dir, marker_venv, "paperfetch_export", {"keys": keys or [], "all": all})
        )

    return server


def run_mcp_server(
    library_dir: Path,
    marker_venv: Path | None = None,
    out_dir: Path | None = None,
    project_files: str = "none",
) -> None:
    library_dir = library_dir.expanduser().resolve()
    marker_venv = (marker_venv or get_default_marker_venv()).expanduser().resolve()

    if _sdk_available():
        server = build_fastmcp_server(library_dir, marker_venv, out_dir, project_files)
        server.run()
        return

    print(
        "[paperfetch] mcp package not installed; using built-in stdio server. "
        "Install with: pip install paperfetch-tool[mcp]",
        file=sys.stderr,
        flush=True,
    )
    from .mcp_stdio import run_stdio_server

    run_stdio_server(library_dir, marker_venv, out_dir, project_files)
