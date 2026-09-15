from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .bundle import bundle_paths
from .config import get_default_marker_venv, get_library_db_path
from .discover import format_discovered, search_semantic_scholar
from .errors import PaperfetchError
from .fetch.http import HttpClient
from .identity import build_identity
from .models import PaperInput
from .resolve.base import resolve
from .runtime import default_fetch_options, fetch_and_record, locked_load_index
from .storage import index_path, list_entries, load_index


def _entry_markdown_path(library_dir: Path, key: str, entry: dict[str, Any]) -> Path | None:
    md_rel = entry.get("md")
    if isinstance(md_rel, str):
        candidate = library_dir / md_rel
        if candidate.exists():
            return candidate
    final = bundle_paths(library_dir, key)
    if final.markdown.exists():
        return final.markdown
    return None


def create_app(library_dir: Path, marker_venv: Path | None = None) -> FastAPI:
    library_dir = library_dir.expanduser().resolve()
    marker_venv = (marker_venv or get_default_marker_venv()).expanduser().resolve()

    app = FastAPI(title="paperfetch", version="0.4.0")

    try:
        from . import service

        if not get_library_db_path(library_dir).exists() and any(
            child.is_dir() and (child / "meta.json").exists() for child in library_dir.iterdir()
        ):
            service.reindex(library_dir)
    except Exception:
        pass

    @app.get("/api/v1/search")
    def search_library(
        q: str,
        key: str | None = None,
        limit: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        from . import service

        return {"results": service.grep(library_dir, q, key=key, limit=limit)}

    @app.get("/api/v1/papers/{key}/outline")
    def paper_outline(key: str) -> dict[str, Any]:
        from . import service

        return {"key": key, "sections": service.outline(library_dir, key)}

    @app.get("/api/v1/papers/{key}/read")
    def paper_read(
        key: str,
        section: str | None = None,
        offset: int = 0,
        max_chars: int = Query(8000, ge=1, le=200000),
    ) -> dict[str, Any]:
        from . import service

        try:
            return service.read(library_dir, key, section=section, offset=offset, max_chars=max_chars)
        except (KeyError, PaperfetchError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/papers/{key}/tables/{ref}")
    def paper_table(key: str, ref: str) -> dict[str, Any]:
        from . import service

        record = service.get_table(library_dir, key, ref)
        if record is None:
            raise HTTPException(status_code=404, detail="Table not found")
        return record

    @app.post("/api/v1/papers/{key}/annotations")
    def add_annotation(key: str, quote: str, note: str = "") -> dict[str, Any]:
        from . import service

        annotation_id = service.annotate(library_dir, key, quote, note)
        return {"id": annotation_id}

    @app.get("/api/v1/papers/{key}/annotations")
    def get_annotations(key: str) -> dict[str, Any]:
        from . import service

        return {"annotations": service.annotations(library_dir, key)}

    @app.get("/api/v1/papers")
    def list_papers(
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        query: str | None = Query(None),
    ) -> dict[str, Any]:
        idx = locked_load_index(library_dir)
        rows = list_entries(idx, limit=None)

        if query:
            q = query.lower()
            rows = [
                (k, v)
                for k, v in rows
                if q in str(v.get("title", "")).lower()
                or q in str(v.get("abstract", "")).lower()
                or q in str(v.get("authors", "")).lower()
            ]

        total = len(rows)
        rows = rows[offset : offset + limit]
        return {"total": total, "limit": limit, "offset": offset, "papers": [{"key": k, **v} for k, v in rows]}

    @app.get("/api/v1/papers/{key}")
    def get_paper(key: str, include_markdown: bool = Query(True)) -> dict[str, Any]:
        idx = locked_load_index(library_dir)
        entry = idx.get(key)
        if not entry:
            raise HTTPException(status_code=404, detail="Paper not found")

        result: dict[str, Any] = {"key": key, **entry}
        if include_markdown:
            md_path = _entry_markdown_path(library_dir, key, entry)
            if md_path is not None:
                result["markdown"] = md_path.read_text(encoding="utf-8", errors="replace")
        return result

    @app.get("/api/v1/papers/{key}/markdown", response_class=PlainTextResponse)
    def get_markdown(key: str) -> str:
        idx = locked_load_index(library_dir)
        entry = idx.get(key)
        if not entry:
            raise HTTPException(status_code=404, detail="Paper not found")
        md_path = _entry_markdown_path(library_dir, key, entry)
        if md_path is None:
            raise HTTPException(status_code=404, detail="Markdown not available")
        return md_path.read_text(encoding="utf-8", errors="replace")

    @app.get("/api/v1/papers/{key}/figures/{name}")
    def get_figure(key: str, name: str) -> FileResponse:
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid figure name")
        final = bundle_paths(library_dir, key)
        candidate = final.figures_dir / name
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="Figure not found")
        return FileResponse(candidate)

    @app.get("/api/v1/library/stats")
    def library_stats() -> dict[str, Any]:
        idx = locked_load_index(library_dir)
        extractors: dict[str, int] = {}
        years: dict[int, int] = {}
        categories: dict[str, int] = {}
        for entry in idx.values():
            ext = str(entry.get("extractor", "unknown"))
            extractors[ext] = extractors.get(ext, 0) + 1
            y = entry.get("year")
            if y:
                years[int(y)] = years.get(int(y), 0) + 1
            for cat in entry.get("categories", []) or []:
                categories[str(cat)] = categories.get(str(cat), 0) + 1
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
        allow_incomplete: bool = False
        download_figures: bool = True
        pdf_visual: bool = True

    @app.post("/api/v1/fetch")
    def fetch_papers(req: FetchRequest) -> dict[str, Any]:
        papers: list[PaperInput] = []
        for url in req.urls:
            title = req.titles.get(url, "")
            papers.append(PaperInput(slug=title or "", title=title, url=url, source="api"))
        if not papers:
            raise HTTPException(status_code=400, detail="No URLs provided")

        options = default_fetch_options(
            library_dir,
            extractor=req.extractor,
            force_download=req.force_download,
            refresh_md=req.refresh_md,
            marker_venv=marker_venv,
            allow_incomplete=req.allow_incomplete,
            download_figures=req.download_figures,
            pdf_visual=req.pdf_visual,
        )
        results = fetch_and_record(papers, options)

        successes = [
            {"key": r.key, "title": r.paper.title or (r.index_entry or {}).get("title"), "index_entry": r.index_entry}
            for r in results
            if r.success
        ]
        failures = [{"key": r.key, "title": r.paper.title, "error": r.error} for r in results if not r.success]
        return {"successes": successes, "failures": failures}

    @app.post("/api/v1/resolve")
    def resolve_endpoint(url: str) -> dict[str, Any]:
        client = HttpClient(timeout=30, retries=1)
        try:
            return resolve(build_identity(url), client).to_dict()
        finally:
            client.close()

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
        except PaperfetchError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

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
        from .bibtex import export_bibtex

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
