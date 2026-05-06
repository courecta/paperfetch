from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .bibtex import export_bibtex
from .config import get_default_marker_venv
from .discover import format_discovered, search_semantic_scholar
from .identity import build_identity
from .marker_backend import ensure_marker_command
from .models import FetchOptions, PaperInput
from .pipeline import run_fetch
from .storage import index_path, load_index


def _send_jsonrpc(id: Any, result: Any | None = None, error: dict[str, Any] | None = None) -> None:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": id}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _send_notification(method: str, params: dict[str, Any]) -> None:
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


TOOLS = [
    {
        "name": "paperfetch_search",
        "description": "Search for academic papers using Semantic Scholar. Returns paper titles, authors, years, and URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results", "default": 10},
                "year": {"type": "string", "description": "Year filter, e.g. '2023' or '2020-2025'", "default": None},
                "open_access": {"type": "boolean", "description": "Only open-access papers", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "paperfetch_fetch",
        "description": "Download and extract papers from URLs. Supports arXiv HTML extraction and marker PDF extraction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of paper URLs",
                },
                "extractor": {
                    "type": "string",
                    "enum": ["auto", "marker", "arxiv_html"],
                    "description": "Extraction method",
                    "default": "auto",
                },
                "force_download": {"type": "boolean", "default": False},
                "refresh_md": {"type": "boolean", "default": False},
            },
            "required": ["urls"],
        },
    },
    {
        "name": "paperfetch_read",
        "description": "Read the extracted markdown content of a cached paper.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Paper key"},
                "url": {"type": "string", "description": "Paper URL (alternative to key)"},
            },
            "required": [],
        },
    },
    {
        "name": "paperfetch_export",
        "description": "Export library entries to BibTeX.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paper keys to export",
                    "default": None,
                },
                "all": {"type": "boolean", "description": "Export entire library", "default": False},
            },
            "required": [],
        },
    },
]


def run_mcp_server(library_dir: Path, marker_venv: Path | None = None) -> None:
    library_dir = library_dir.expanduser().resolve()
    marker_venv = (marker_venv or get_default_marker_venv()).expanduser().resolve()

    # Send initialization notification
    _send_notification(
        "notifications/tools/list_changed",
        {"tools": TOOLS},
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            _send_jsonrpc(
                req_id,
                result={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "paperfetch", "version": "0.3.0"},
                },
            )
            continue

        if method == "tools/list":
            _send_jsonrpc(req_id, result={"tools": TOOLS})
            continue

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            try:
                if tool_name == "paperfetch_search":
                    year = arguments.get("year")
                    year_start: int | None = None
                    year_end: int | None = None
                    if year:
                        parts = str(year).split("-")
                        if len(parts) == 1:
                            year_start = year_end = int(parts[0])
                        elif len(parts) == 2:
                            year_start = int(parts[0]) if parts[0] else None
                            year_end = int(parts[1]) if parts[1] else None

                    papers = search_semantic_scholar(
                        query=arguments["query"],
                        limit=arguments.get("limit", 10),
                        year_start=year_start,
                        year_end=year_end,
                        open_access_only=arguments.get("open_access", False),
                    )
                    _send_jsonrpc(req_id, result={
                        "content": [{"type": "text", "text": format_discovered(papers, "json")}]
                    })

                elif tool_name == "paperfetch_fetch":
                    urls = arguments.get("urls", [])
                    extractor = arguments.get("extractor", "auto")
                    force_download = arguments.get("force_download", False)
                    refresh_md = arguments.get("refresh_md", False)

                    marker_cmd: str | None = None
                    if extractor != "arxiv_html":
                        marker_cmd = ensure_marker_command(
                            marker_venv=marker_venv,
                            allow_install=True,
                            verbose=False,
                        )

                    papers = [
                        PaperInput(slug="", title="", url=u, source="mcp")
                        for u in urls
                    ]
                    options = FetchOptions(
                        library_dir=library_dir,
                        out_dir=None,
                        project_files="none",
                        link_mode="copy",
                        overwrite=False,
                        force_download=force_download,
                        refresh_md=refresh_md,
                        dry_run=False,
                        download_timeout=120,
                        marker_timeout=900,
                        download_retries=2,
                        marker_retries=1,
                        download_backoff=1.5,
                        marker_backoff=2.0,
                        workers=4,
                        min_md_chars=300,
                        min_md_lines=8,
                        allow_low_quality_md=False,
                        verbose=False,
                        extractor=extractor,
                    )
                    snapshot = load_index(index_path(library_dir))
                    results = run_fetch(papers, options, marker_cmd, snapshot)

                    output = []
                    for r in results:
                        if r.success:
                            output.append(f"[ok] {r.paper.title} ({r.key})")
                        else:
                            output.append(f"[fail] {r.paper.title}: {r.error}")

                    _send_jsonrpc(req_id, result={
                        "content": [{"type": "text", "text": "\n".join(output)}]
                    })

                elif tool_name == "paperfetch_read":
                    idx = load_index(index_path(library_dir))
                    key = arguments.get("key")
                    url = arguments.get("url")
                    if url and not key:
                        key = build_identity(url).key

                    if not key:
                        _send_jsonrpc(req_id, error={"code": -32602, "message": "Provide key or url"})
                        continue

                    entry = idx.get(key)
                    if not entry:
                        _send_jsonrpc(req_id, error={"code": -32602, "message": "Paper not found"})
                        continue

                    md_rel = entry.get("md")
                    if md_rel:
                        md_path = library_dir / md_rel
                        if md_path.exists():
                            content = md_path.read_text(encoding="utf-8")
                            _send_jsonrpc(req_id, result={
                                "content": [{"type": "text", "text": content}]
                            })
                            continue

                    _send_jsonrpc(req_id, error={"code": -32602, "message": "Markdown not available"})

                elif tool_name == "paperfetch_export":
                    idx = load_index(index_path(library_dir))
                    keys: set[str] = set(arguments.get("keys", []))
                    if arguments.get("all"):
                        keys.update(idx.keys())

                    if not keys:
                        _send_jsonrpc(req_id, error={"code": -32602, "message": "No keys specified"})
                        continue

                    entries = [idx[k] for k in keys if k in idx]
                    if not entries:
                        _send_jsonrpc(req_id, error={"code": -32602, "message": "No matching entries"})
                        continue

                    _send_jsonrpc(req_id, result={
                        "content": [{"type": "text", "text": export_bibtex(entries)}]
                    })

                else:
                    _send_jsonrpc(req_id, error={"code": -32601, "message": f"Unknown tool: {tool_name}"})

            except Exception as exc:
                _send_jsonrpc(req_id, error={"code": -32603, "message": str(exc)})

            continue

        # Unknown method
        _send_jsonrpc(req_id, error={"code": -32601, "message": f"Unknown method: {method}"})
