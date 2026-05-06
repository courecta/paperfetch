from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .bibtex import export_bibtex
from .config import get_default_marker_venv
from .discover import format_discovered, search_semantic_scholar
from .identity import build_identity
from .marker_backend import ensure_marker_command
from .models import FetchOptions, PaperInput
from .pipeline import run_fetch
from .storage import index_path, list_entries, load_index


def create_app(library_dir: Path, marker_venv: Path | None = None) -> FastAPI:
    library_dir = library_dir.expanduser().resolve()
    marker_venv = (marker_venv or get_default_marker_venv()).expanduser().resolve()

    app = FastAPI(title="paperfetch", version="0.3.0")

    @app.get("/api/v1/papers")
    def list_papers(
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        query: str | None = Query(None),
    ) -> dict[str, Any]:
        idx = load_index(index_path(library_dir))
        rows = list_entries(idx, limit=None)

        if query:
            q = query.lower()
            rows = [
                (k, v) for k, v in rows
                if q in str(v.get("title", "")).lower()
                or q in str(v.get("abstract", "")).lower()
                or q in str(v.get("authors", "")).lower()
            ]

        total = len(rows)
        rows = rows[offset:offset + limit]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "papers": [{"key": k, **v} for k, v in rows],
        }

    @app.get("/api/v1/papers/{key}")
    def get_paper(key: str) -> dict[str, Any]:
        idx = load_index(index_path(library_dir))
        entry = idx.get(key)
        if not entry:
            raise HTTPException(status_code=404, detail="Paper not found")

        result: dict[str, Any] = {"key": key, **entry}

        # Include markdown content if available
        md_rel = entry.get("md")
        if isinstance(md_rel, str):
            md_path = library_dir / md_rel
            if md_path.exists():
                result["markdown"] = md_path.read_text(encoding="utf-8")

        return result

    @app.get("/api/v1/library/stats")
    def library_stats() -> dict[str, Any]:
        idx = load_index(index_path(library_dir))
        extractors: dict[str, int] = {}
        years: dict[int, int] = {}
        categories: dict[str, int] = {}

        for entry in idx.values():
            ext = entry.get("extractor", "unknown")
            extractors[ext] = extractors.get(ext, 0) + 1
            y = entry.get("year")
            if y:
                years[y] = years.get(y, 0) + 1
            for cat in entry.get("categories", []):
                categories[cat] = categories.get(cat, 0) + 1

        return {
            "total_papers": len(idx),
            "by_extractor": extractors,
            "by_year": dict(sorted(years.items())),
            "by_category": dict(sorted(categories.items(), key=lambda kv: kv[1], reverse=True)),
        }

    class FetchRequest(BaseModel):
        urls: list[str] = Field(default_factory=list)
        titles: dict[str, str] = Field(default_factory=dict)
        extractor: str = "auto"
        force_download: bool = False
        refresh_md: bool = False

    @app.post("/api/v1/fetch")
    def fetch_papers(req: FetchRequest) -> dict[str, Any]:
        marker_cmd: str | None = None
        if req.extractor != "arxiv_html":
            marker_cmd = ensure_marker_command(
                marker_venv=marker_venv,
                allow_install=True,
                verbose=False,
            )
            if marker_cmd is None and req.extractor == "marker":
                raise HTTPException(status_code=500, detail="Marker not available")

        papers: list[PaperInput] = []
        for url in req.urls:
            papers.append(PaperInput(
                slug=req.titles.get(url, ""),
                title=req.titles.get(url, ""),
                url=url,
                source="api",
            ))

        if not papers:
            raise HTTPException(status_code=400, detail="No URLs provided")

        options = FetchOptions(
            library_dir=library_dir,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=req.force_download,
            refresh_md=req.refresh_md,
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
            extractor=req.extractor,
        )

        snapshot = load_index(index_path(library_dir))
        results = run_fetch(papers, options, marker_cmd, snapshot)

        successes = []
        failures = []
        for r in results:
            if r.success:
                successes.append({"key": r.key, "title": r.paper.title, "index_entry": r.index_entry})
            else:
                failures.append({"key": r.key, "title": r.paper.title, "error": r.error})

        return {"successes": successes, "failures": failures}

    @app.post("/api/v1/discover")
    def discover_papers(
        query: str,
        limit: int = 20,
        year: str | None = None,
        open_access: bool = False,
        venue: str | None = None,
        output_format: str = "json",
    ) -> Any:
        year_start: int | None = None
        year_end: int | None = None
        if year:
            parts = year.split("-")
            if len(parts) == 1:
                year_start = year_end = int(parts[0])
            elif len(parts) == 2:
                year_start = int(parts[0]) if parts[0] else None
                year_end = int(parts[1]) if parts[1] else None

        try:
            papers = search_semantic_scholar(
                query=query,
                limit=limit,
                year_start=year_start,
                year_end=year_end,
                open_access_only=open_access,
                venue=venue,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc))

        if output_format == "urls":
            return PlainTextResponse(format_discovered(papers, "urls"))
        if output_format == "tsv":
            return PlainTextResponse(format_discovered(papers, "tsv"), media_type="text/tab-separated-values")
        return {"papers": json.loads(format_discovered(papers, "json"))}

    @app.get("/api/v1/export/bibtex")
    def export_bibtex_endpoint(
        key: list[str] = Query(default_factory=list),
        all: bool = Query(False),
    ) -> PlainTextResponse:
        idx = load_index(index_path(library_dir))
        keys = set(key)
        if all:
            keys.update(idx.keys())

        if not keys:
            raise HTTPException(status_code=400, detail="No keys specified. Use ?key=... or ?all=true")

        entries = [idx[k] for k in keys if k in idx]
        if not entries:
            raise HTTPException(status_code=404, detail="No matching entries")

        return PlainTextResponse(export_bibtex(entries), media_type="application/x-bibtex")

    return app
