from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from . import service
from .bibtex import export_bibtex
from .bundle import bundle_paths
from .discover import format_discovered, search_semantic_scholar
from .errors import PaperfetchError
from .identity import build_identity
from .models import PaperInput
from .runtime import default_fetch_options, locked_load_index

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_MAX_CHARS = 8000

TOOLS = [
    {
        "name": "paperfetch_search",
        "description": "Search for academic papers using Semantic Scholar. Returns titles, authors, years, and URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results", "default": 10},
                "year": {"type": "string", "description": "Year filter, e.g. '2023' or '2020-2025'"},
                "open_access": {"type": "boolean", "description": "Only open-access papers", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "paperfetch_fetch",
        "description": "Download and extract papers into the local library. Figures and tables are preserved in the bundle.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}, "description": "Paper URLs or identifiers"},
                "extractor": {"type": "string", "enum": ["auto", "arxiv_html", "marker"], "default": "auto"},
                "force_download": {"type": "boolean", "default": False},
                "refresh_md": {"type": "boolean", "default": False},
                "background": {
                    "type": "boolean",
                    "default": True,
                    "description": "Start in the background and return a job id. Extraction takes minutes per paper, so a foreground fetch of more than one or two papers will outlast the client's tool-call timeout.",
                },
            },
            "required": ["urls"],
        },
    },
    {
        "name": "paperfetch_jobs",
        "description": "Check background ingestion jobs started by paperfetch_fetch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Omit to list recent jobs"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": [],
        },
    },
    {
        "name": "paperfetch_outline",
        "description": "List the section outline of a cached paper with character counts.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Paper key"}},
            "required": ["key"],
        },
    },
    {
        "name": "paperfetch_read",
        "description": "Read extracted markdown, optionally limited to one section, with pagination (offset/next_offset).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Paper key"},
                "url": {"type": "string", "description": "Paper URL (alternative to key)"},
                "section": {"type": "string", "description": "Optional section id from paperfetch_outline"},
                "offset": {"type": "integer", "description": "Character offset to start from", "default": 0},
                "max_chars": {"type": "integer", "description": "Maximum characters to return", "default": 8000},
            },
            "required": [],
        },
    },
    {
        "name": "paperfetch_figure",
        "description": "Return a figure image plus caption from a cached paper.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Paper key"},
                "figure": {"type": "string", "description": "Figure id or label, e.g. S4.F1 or 'Figure 1'"},
            },
            "required": ["key", "figure"],
        },
    },
    {
        "name": "paperfetch_table",
        "description": "Return a table (HTML and CSV) plus caption from a cached paper.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Paper key"},
                "table": {"type": "string", "description": "Table id or label, e.g. S4.T1 or 'Table 1'"},
            },
            "required": ["key", "table"],
        },
    },
    {
        "name": "paperfetch_grep",
        "description": "Full-text search across cached papers (SQLite FTS5).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "FTS5 query"},
                "key": {"type": "string", "description": "Restrict to one paper key"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "paperfetch_annotate",
        "description": "Save a quote/note attached to a cached paper.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "quote": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["key", "quote"],
        },
    },
    {
        "name": "paperfetch_export",
        "description": "Export library entries to BibTeX.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}, "default": None},
                "key": {"type": "string", "description": "Single key; alias for keys"},
                "all": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    },
]


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _send_result(req_id: Any, result: Any) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _send_error(req_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _text_content(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _window(text: str, offset: int, max_chars: int) -> str:
    if max_chars <= 0:
        max_chars = DEFAULT_MAX_CHARS
    start = max(0, int(offset))
    chunk = text[start : start + max_chars]
    next_offset = start + len(chunk)
    if next_offset < len(text):
        chunk += f"\n\n[next_offset={next_offset} of {len(text)} chars]"
    return chunk


def _load_markdown(library_dir: Path, key: str) -> str | None:
    idx = locked_load_index(library_dir)
    entry = idx.get(key) or {}
    md_rel = entry.get("md")
    candidates: list[Path] = []
    if isinstance(md_rel, str):
        candidates.append(library_dir / md_rel)
    candidates.append(bundle_paths(library_dir, key).markdown)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    return None


def _figure_payload(library_dir: Path, key: str, figure_ref: str) -> list[dict[str, Any]]:
    record = service.get_figure(library_dir, key, figure_ref)
    if record is None:
        raise PaperfetchError(f"Figure {figure_ref!r} not found")

    content: list[dict[str, Any]] = []
    caption = str(record.get("caption") or "").strip()
    label = record.get("label") or record.get("id")
    for image_path_str in record.get("image_paths", []):
        image_path = Path(str(image_path_str))
        if not image_path.is_file():
            continue
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        mime = "image/png"
        suffix = image_path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".svg":
            mime = "image/svg+xml"
        content.append({"type": "image", "data": data, "mimeType": mime})
    content.append(_text_content(f"{label}: {caption}".strip(": ")))
    if not any(item["type"] == "image" for item in content):
        raise PaperfetchError(f"Figure {figure_ref!r} has no stored image")
    return content


def _handle_tool(
    library_dir: Path,
    marker_venv: Path,
    name: str,
    arguments: dict[str, Any],
    out_dir: Path | None = None,
    project_files: str = "none",
) -> list[dict[str, Any]]:
    if name == "paperfetch_search":
        year = arguments.get("year")
        year_start = year_end = None
        if year:
            parts = str(year).split("-")
            if len(parts) == 1:
                year_start = year_end = int(parts[0])
            elif len(parts) == 2:
                year_start = int(parts[0]) if parts[0] else None
                year_end = int(parts[1]) if parts[1] else None
        papers = search_semantic_scholar(
            query=arguments["query"],
            limit=int(arguments.get("limit", 10)),
            year_start=year_start,
            year_end=year_end,
            open_access_only=bool(arguments.get("open_access", False)),
        )
        return [_text_content(format_discovered(papers, "json"))]

    if name == "paperfetch_fetch":
        urls = list(arguments.get("urls", []))
        if not urls:
            raise PaperfetchError("No URLs provided")
        options = default_fetch_options(
            library_dir,
            extractor=str(arguments.get("extractor", "auto")),
            force_download=bool(arguments.get("force_download", False)),
            refresh_md=bool(arguments.get("refresh_md", False)),
            marker_venv=marker_venv,
            out_dir=out_dir,
            project_files=project_files,
        )
        if arguments.get("background", True):
            from .jobs import describe, start_fetch_job

            record = start_fetch_job(
                library_dir,
                urls,
                extractor=str(arguments.get("extractor", "auto")),
                out_dir=out_dir,
                project_files=project_files,
                marker_venv=marker_venv,
            )
            return [
                _text_content(
                    f"Ingestion started in the background. {describe(record)}\n"
                    f"Poll with paperfetch_jobs(job_id=\"{record['id']}\"). "
                    "Expect roughly 1-2 minutes per paper."
                )
            ]

        papers = [PaperInput(slug="", title="", url=url, source="mcp") for url in urls]
        results = service.fetch_papers(library_dir, papers, options)
        lines = []
        for result in results:
            if result.success:
                entry = result.index_entry or {}
                lines.append(
                    f"[ok] key={result.key} title={entry.get('title') or result.paper.url} "
                    f"extractor={entry.get('extractor')} coverage={(result.coverage or {}).get('ratio')}"
                )
            else:
                lines.append(f"[fail] {result.paper.url}: {result.error}")
        return [_text_content("\n".join(lines))]

    if name == "paperfetch_outline":
        key = str(arguments.get("key") or "")
        if not key:
            raise PaperfetchError("Provide key")
        sections = service.outline(library_dir, key)
        lines = []
        for section in sections:
            indent = "  " * max(0, int(section.get("level") or 1) - 1)
            lines.append(
                f"{indent}- {section.get('title')} (id={section.get('section_id')}, chars={section.get('char_count')})"
            )
        return [_text_content("\n".join(lines) or "No outline available")]

    if name == "paperfetch_read":
        key = arguments.get("key")
        if not key and arguments.get("url"):
            key = build_identity(str(arguments["url"])).key
        if not key:
            raise PaperfetchError("Provide key or url")
        result = service.read(
            library_dir,
            str(key),
            section=arguments.get("section"),
            offset=int(arguments.get("offset", 0)),
            max_chars=int(arguments.get("max_chars", DEFAULT_MAX_CHARS)),
        )
        header = (
            f"[{result['key']} offset={result['offset']} next_offset={result['next_offset']} "
            f"total_chars={result['total_chars']}]\n"
        )
        return [_text_content(header + result["text"])]

    if name == "paperfetch_figure":
        return _figure_payload(library_dir, str(arguments["key"]), str(arguments["figure"]))

    if name == "paperfetch_table":
        record = service.get_table(library_dir, str(arguments["key"]), str(arguments["table"]))
        if record is None:
            raise PaperfetchError(f"Table {arguments.get('table')!r} not found")
        parts = [f"{record.get('label') or record.get('id')}: {record.get('caption') or ''}".strip(": ")]
        if record.get("html_path"):
            parts.append(Path(str(record["html_path"])).read_text(encoding="utf-8", errors="replace"))
        elif record.get("csv_path"):
            parts.append(Path(str(record["csv_path"])).read_text(encoding="utf-8", errors="replace"))
        elif record.get("html_text"):
            parts.append(str(record["html_text"]))
        elif record.get("csv_text"):
            parts.append(str(record["csv_text"]))
        else:
            raise PaperfetchError("Table has no stored representation")
        content: list[dict[str, Any]] = [_text_content("\n\n".join(parts))]
        crop = record.get("crop_path")
        if crop and Path(str(crop)).is_file():
            data = base64.b64encode(Path(str(crop)).read_bytes()).decode("ascii")
            content.insert(0, {"type": "image", "data": data, "mimeType": "image/png"})
        return content

    if name == "paperfetch_grep":
        rows = service.grep(
            library_dir,
            str(arguments["query"]),
            key=arguments.get("key"),
            limit=int(arguments.get("limit", 20)),
        )
        lines = [
            f"{row['paper_key']} [{row.get('section_id') or '-'}]: {row.get('snippet', '')}" for row in rows
        ]
        return [_text_content("\n".join(lines) or "No matches")]

    if name == "paperfetch_annotate":
        annotation_id = service.annotate(
            library_dir,
            str(arguments["key"]),
            str(arguments.get("quote", "")),
            str(arguments.get("note", "")),
        )
        return [_text_content(f"Saved annotation #{annotation_id}")]

    if name == "paperfetch_jobs":
        from .jobs import describe, get_job, list_jobs

        job_id = arguments.get("job_id")
        if job_id:
            record = get_job(library_dir, str(job_id))
            if record is None:
                raise PaperfetchError(f"Unknown job: {job_id}")
            lines = [describe(record)]
            for row in record.get("results", []):
                mark = "ok" if row.get("ok") else "fail"
                lines.append(f"  [{mark}] {row.get('title') or row.get('url')}" + (
                    f" - {row['error']}" if row.get("error") else ""
                ))
            return [_text_content("\n".join(lines))]

        records = list_jobs(library_dir, limit=int(arguments.get("limit", 10)))
        if not records:
            return [_text_content("No ingestion jobs recorded.")]
        return [_text_content("\n".join(describe(r) for r in records))]

    if name == "paperfetch_export":
        idx = locked_load_index(library_dir)
        keys: set[str] = set(arguments.get("keys") or [])
        # Every other tool takes a singular "key"; accept it here too rather
        # than making callers remember which one is plural.
        if arguments.get("key"):
            keys.add(str(arguments["key"]))
        if arguments.get("all"):
            keys.update(idx.keys())
        if not keys:
            raise PaperfetchError(
                "No keys specified",
                hint='Pass {"keys": ["<key>"]}, {"key": "<key>"}, or {"all": true}.',
            )
        entries = [idx[key] for key in keys if key in idx]
        if not entries:
            raise PaperfetchError("No matching entries")
        return [_text_content(export_bibtex(entries))]

    raise PaperfetchError(f"Unknown tool: {name}")


def server_instructions(library_dir: Path, out_dir: Path | None, project_files: str) -> str:
    """Orientation sent to the model when the client connects.

    MCP's initialize response carries this, so an agent learns how the library
    is laid out before its first call rather than after a mistake.
    """
    lines = [
        "paperfetch serves a local library of academic papers extracted losslessly: "
        "sections, figures with captions, tables with their spans preserved, equations as LaTeX, "
        "and page crops of each float.",
        "",
        f"Library: {library_dir}",
    ]
    if out_dir is not None and project_files != "none":
        lines += [
            f"Project copies: {out_dir}",
            "",
            "IMPORTANT: files under the project directory are hardlinks into the shared library, "
            "not independent copies. Editing one in place rewrites the bytes every project and the "
            "library itself see. Deleting a link is safe; modifying content is not. Treat them as "
            "read-only, and write any derived notes to a separate file.",
        ]
    lines += [
        "",
        "How to work with it:",
        "- Never guess what a paper says. Quote the extracted text and cite the bundle key.",
        "- Read in stages: paperfetch_outline to see sections and their sizes, then paperfetch_read "
        "with a section id, paging via offset/next_offset. Do not pull a whole paper into context.",
        "- paperfetch_grep searches the full text of every paper at once; prefer it to reading files.",
        "- paperfetch_figure returns the image itself, and paperfetch_table the real table rather "
        "than a markdown approximation.",
        "- Ingestion runs in the background because extraction takes 1-2 minutes per paper. "
        "paperfetch_fetch returns a job id; report that to the user and poll paperfetch_jobs "
        "instead of waiting.",
        "- The library is shared with other projects, so it may contain papers unrelated to the "
        "current work. Filter by what you are asked about.",
    ]
    return "\n".join(lines)


def run_stdio_server(
    library_dir: Path,
    marker_venv: Path | None = None,
    out_dir: Path | None = None,
    project_files: str = "none",
) -> None:
    from .config import VERSION, get_default_marker_venv

    library_dir = library_dir.expanduser().resolve()
    marker_venv = (marker_venv or get_default_marker_venv()).expanduser().resolve()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method == "initialize":
            _send_result(
                req_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "paperfetch", "version": VERSION},
                    "instructions": server_instructions(library_dir, out_dir, project_files),
                },
            )
            continue

        if method in {"notifications/initialized", "notifications/cancelled"}:
            continue

        if method == "ping":
            _send_result(req_id, {})
            continue

        if method == "tools/list":
            _send_result(req_id, {"tools": TOOLS})
            continue

        if method == "tools/call":
            tool_name = str(params.get("name", ""))
            arguments = params.get("arguments", {}) or {}
            try:
                content = _handle_tool(library_dir, marker_venv, tool_name, arguments, out_dir, project_files)
                _send_result(req_id, {"content": content})
            except PaperfetchError as exc:
                _send_error(req_id, -32602, str(exc))
            except Exception as exc:  # noqa: BLE001
                _send_error(req_id, -32603, str(exc))
            continue

        if req_id is not None:
            _send_error(req_id, -32601, f"Unknown method: {method}")
