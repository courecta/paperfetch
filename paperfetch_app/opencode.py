from __future__ import annotations

import json
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


def _config_path(scope: str, project_dir: Path) -> Path:
    if scope == "global":
        return Path.home() / ".config" / "opencode" / "opencode.json"
    return project_dir / "opencode.json"


def _skill_path(scope: str, project_dir: Path) -> Path:
    if scope == "global":
        return Path.home() / ".config" / "opencode" / "skills" / SKILL_NAME / "SKILL.md"
    return project_dir / ".opencode" / "skills" / SKILL_NAME / "SKILL.md"


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
    scope: str = "project",
    project_dir: Path | None = None,
    library_dir: Path | None = None,
    marker_venv: Path | None = None,
    install_skill: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    project_dir = (project_dir or Path.cwd()).expanduser().resolve()
    library_dir = (library_dir or get_default_library_dir()).expanduser().resolve()
    marker_venv = (marker_venv or get_default_marker_venv()).expanduser().resolve()

    config_path = _config_path(scope, project_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and force:
        config = {}
    else:
        config = _load_config(config_path)

    config.setdefault("$schema", "https://opencode.ai/config.json")
    command = _binary_command() + [
        "mcp",
        "--library-dir",
        str(library_dir),
        "--marker-venv",
        str(marker_venv),
    ]
    environment: dict[str, str] = {}
    mailto = get_mailto()
    if mailto:
        environment["PAPERFETCH_MAILTO"] = mailto

    mcp = config.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        raise ValueError("Existing 'mcp' key in config is not an object")
    mcp[SKILL_NAME] = {
        "type": "local",
        "command": command,
        "enabled": True,
        **({"environment": environment} if environment else {}),
    }

    tmp_path = config_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(config_path)

    skill_path = None
    if install_skill:
        skill_path = _skill_path(scope, project_dir)
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(SKILL_TEMPLATE, encoding="utf-8")

    return {
        "config_path": str(config_path),
        "skill_path": str(skill_path) if skill_path else None,
        "server": "paperfetch",
        "command": command,
        "library_dir": str(library_dir),
        "version": VERSION,
    }
