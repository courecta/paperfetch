from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .config import VERSION, get_default_library_dir, get_default_marker_venv, get_mailto

SKILL_NAME = "paperfetch"

SKILL_TEMPLATE = """---
name: paperfetch
description: Use when the user asks to find, fetch, read, summarize, or cite academic papers (arXiv, DOI, OpenReview, PMC, conference proceedings). Manages a local paper library with lossless bundles (figures, tables, equations) and exposes full-text search.
---

# paperfetch

Local research-paper library with lossless bundles. Each paper bundle contains
`paper.md`, `paper.txt`, `paper.pdf`, `document.json` (structured IR),
`refs.bib`, `figures/`, `tables/`, `crops/`, and `source.html`.

## CLI

```bash
paperfetch discover "query terms" --limit 10          # search Semantic Scholar
paperfetch fetch --url https://arxiv.org/abs/2501.00001
paperfetch fetch --manifest papers.csv --out-dir ./papers
paperfetch list
paperfetch inspect --key <key>
paperfetch grep "exact phrase"                        # FTS5 over everything
paperfetch export --all -o references.bib
```

Fetching a paper into the current project:

```bash
paperfetch fetch --url <url> --out-dir ./papers --project-files both
```

This writes `papers/<slug>.md` plus `papers/<slug>-figures/`; read the
markdown, and view figure PNGs directly (local images are readable).

## MCP tools (when the paperfetch MCP server is configured)

- `paperfetch_search` — Semantic Scholar search.
- `paperfetch_fetch` — download and extract into the library.
- `paperfetch_outline` — section outline with ids and char counts.
- `paperfetch_read` — windowed read; pass `section` from the outline and use
  `offset`/`next_offset` to page through long papers.
- `paperfetch_figure` — returns the figure image(s) plus caption.
- `paperfetch_table` — canonical HTML (or CSV fallback) plus caption.
- `paperfetch_grep` — full-text search across the library.
- `paperfetch_annotate` — save quotes/notes.
- `paperfetch_export` — BibTeX export.

## The library is shared

Papers live in one library (default `~/papers/library`) that every project on
this machine draws from. Files in a project's `papers/` directory are
**hardlinks into that library, not copies**: editing one in place rewrites the
bytes the library and every other project see. Deleting a link is safe;
modifying content is not. Treat them as read-only and write derived notes
elsewhere.

Because the library is shared it may hold papers unrelated to this project.
Filter by what you were asked about rather than assuming everything is
relevant.

## Ingestion runs in the background

Extraction takes 1-2 minutes per paper, longer than a tool call should block.
`paperfetch_fetch` returns a job id immediately; tell the user it started and
poll `paperfetch_jobs` rather than waiting. Use the CLI for large batches.

## Workflow

1. `paperfetch_search` to find candidates, then `paperfetch_fetch` the chosen URLs.
2. `paperfetch_outline` to plan reading; never pull the whole paper at once.
3. `paperfetch_read` section by section; use figures/tables tools for visuals.
4. `paperfetch_grep` for cross-paper evidence and `paperfetch_export` for citations.

Never guess paper facts from memory when a bundled paper is available; quote
the extracted text and cite the bundle key.
"""


def _binary_command() -> list[str]:
    executable = shutil.which("paperfetch")
    if executable:
        return [executable]
    return [sys.executable, "-m", "paperfetch_app"]


CLIENTS = ("opencode", "claude-code", "claude-desktop")


def _desktop_config_dir() -> Path:
    """Claude Desktop's config location is platform-specific."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude"
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        return Path(base) / "Claude" if base else Path.home() / "AppData" / "Roaming" / "Claude"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude"


def _config_path(client: str, scope: str, project_dir: Path) -> Path:
    if client == "opencode":
        if scope == "global":
            return Path.home() / ".config" / "opencode" / "opencode.json"
        return project_dir / "opencode.json"
    if client == "claude-code":
        # Project scope is .mcp.json, which is meant to be committed; global
        # scope lives in the user-wide ~/.claude.json.
        if scope == "global":
            return Path.home() / ".claude.json"
        return project_dir / ".mcp.json"
    if client == "claude-desktop":
        # Claude Desktop has no per-project config.
        return _desktop_config_dir() / "claude_desktop_config.json"
    raise ValueError(f"Unknown client: {client}")


def _skill_path(client: str, scope: str, project_dir: Path) -> Path | None:
    if client == "opencode":
        if scope == "global":
            return Path.home() / ".config" / "opencode" / "skills" / SKILL_NAME / "SKILL.md"
        return project_dir / ".opencode" / "skills" / SKILL_NAME / "SKILL.md"
    if client == "claude-code":
        if scope == "global":
            return Path.home() / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
        return project_dir / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
    # Claude Desktop loads skills from its own UI, not from disk.
    return None


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Existing config at {path} is not valid JSON: {exc}. "
            "Merge the paperfetch entry manually or use --force to overwrite."
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Existing config at {path} is not a JSON object")
    return payload


def install_mcp(
    *,
    client: str = "opencode",
    scope: str = "project",
    project_dir: Path | None = None,
    library_dir: Path | None = None,
    marker_venv: Path | None = None,
    out_dir: Path | None = None,
    project_files: str = "both",
    install_skill: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    if client not in CLIENTS:
        raise ValueError(f"Unknown client {client!r}; expected one of {', '.join(CLIENTS)}")
    if client == "claude-desktop" and scope == "project":
        # Claude Desktop reads one config for the whole app.
        scope = "global"

    project_dir = (project_dir or Path.cwd()).expanduser().resolve()
    library_dir = (library_dir or get_default_library_dir()).expanduser().resolve()
    marker_venv = (marker_venv or get_default_marker_venv()).expanduser().resolve()

    config_path = _config_path(client, scope, project_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and force:
        config = {}
    else:
        config = _load_config(config_path)

    binary, *binary_args = _binary_command()
    args = [
        *binary_args,
        "mcp",
        "--library-dir",
        str(library_dir),
        "--marker-venv",
        str(marker_venv),
    ]
    if out_dir is not None:
        # Without this the agent's fetches land only in the content-addressed
        # library; with it, readable copies also appear in the project.
        args += ["--out-dir", str(out_dir.expanduser().resolve()), "--project-files", project_files]
    environment: dict[str, str] = {}
    mailto = get_mailto()
    if mailto:
        environment["PAPERFETCH_MAILTO"] = mailto

    if client == "opencode":
        config.setdefault("$schema", "https://opencode.ai/config.json")
        servers = config.setdefault("mcp", {})
        if not isinstance(servers, dict):
            raise ValueError("Existing 'mcp' key in config is not an object")
        servers[SKILL_NAME] = {
            "type": "local",
            "command": [binary, *args],
            "enabled": True,
            **({"environment": environment} if environment else {}),
        }
    else:
        # Both Claude clients use the same mcpServers schema.
        servers = config.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("Existing 'mcpServers' key in config is not an object")
        servers[SKILL_NAME] = {
            "command": binary,
            "args": args,
            **({"env": environment} if environment else {}),
        }

    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(config_path)

    skill_path = None
    if install_skill:
        target = _skill_path(client, scope, project_dir)
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(SKILL_TEMPLATE, encoding="utf-8")
            skill_path = target

    return {
        "config_path": str(config_path),
        "skill_path": str(skill_path) if skill_path else None,
        "server": "paperfetch",
        "client": client,
        "scope": scope,
        "library_dir": str(library_dir),
        "out_dir": str(out_dir.expanduser().resolve()) if out_dir else None,
        "version": VERSION,
    }
