from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import db as db_module
from .bundle import bundle_paths
from .config import get_library_db_path
from .models import FetchOptions, PaperInput, ProcessResult
from .runtime import fetch_and_record


def connect(library_dir: Path) -> sqlite3.Connection:
    conn = db_module.connect(get_library_db_path(library_dir))
    db_module.init_db(conn)
    row = conn.execute("SELECT COUNT(*) FROM papers").fetchone()
    if row is not None and int(row[0]) == 0:
        db_module.rebuild(conn, library_dir)
    return conn


def reindex(library_dir: Path, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    own = conn is None
    conn = conn or connect(library_dir)
    try:
        return db_module.rebuild(conn, library_dir)
    finally:
        if own:
            conn.close()


def index_key(library_dir: Path, key: str, conn: sqlite3.Connection | None = None) -> bool:
    own = conn is None
    conn = conn or connect(library_dir)
    try:
        return db_module.index_bundle(conn, library_dir, key)
    finally:
        if own:
            conn.close()


def fetch_papers(
    library_dir: Path,
    papers: list[PaperInput],
    options: FetchOptions | None = None,
) -> list[ProcessResult]:
    from .runtime import default_fetch_options

    options = options or default_fetch_options(library_dir)
    results = fetch_and_record(papers, options)
    conn = connect(library_dir)
    try:
        for result in results:
            if result.success and not result.dry_run:
                db_module.index_bundle(conn, library_dir, result.key)
    finally:
        conn.close()
    return results


def list_papers(
    library_dir: Path,
    *,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    conn = connect(library_dir)
    try:
        total, rows = db_module.list_papers(conn, query=query, limit=limit, offset=offset)
        return {"total": total, "papers": rows}
    finally:
        conn.close()


def get_paper(library_dir: Path, key: str) -> dict[str, Any] | None:
    conn = connect(library_dir)
    try:
        return db_module.get_paper(conn, key)
    finally:
        conn.close()


def outline(library_dir: Path, key: str) -> list[dict[str, Any]]:
    conn = connect(library_dir)
    try:
        return db_module.outline(conn, key)
    finally:
        conn.close()


def read(
    library_dir: Path,
    key: str,
    *,
    section: str | None = None,
    offset: int = 0,
    max_chars: int = 8000,
) -> dict[str, Any]:
    if max_chars <= 0:
        max_chars = 8000
    if section:
        conn = connect(library_dir)
        try:
            blocks = db_module.read_section(conn, key, section)
        finally:
            conn.close()
        if not blocks:
            from .errors import NotFoundError

            raise NotFoundError(f"Section {section!r} not found for paper {key}")
        text = "\n\n".join(str(block["text"]) for block in blocks if block.get("text"))
    else:
        final = bundle_paths(library_dir, key)
        candidates = [final.markdown, final.text]
        text = ""
        for candidate in candidates:
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8", errors="replace")
                break
        if not text:
            from .errors import NotFoundError

            raise NotFoundError(f"No text available for paper {key}")

    start = max(0, int(offset))
    chunk = text[start : start + max_chars]
    next_offset = start + len(chunk)
    return {
        "key": key,
        "section": section,
        "offset": start,
        "next_offset": next_offset if next_offset < len(text) else None,
        "total_chars": len(text),
        "text": chunk,
    }


def grep(
    library_dir: Path,
    query: str,
    *,
    key: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    conn = connect(library_dir)
    try:
        return db_module.search(conn, query, paper_key=key, limit=limit)
    finally:
        conn.close()


def get_figure(library_dir: Path, key: str, figure_ref: str) -> dict[str, Any] | None:
    conn = connect(library_dir)
    try:
        record = db_module.get_figure(conn, key, figure_ref)
    finally:
        conn.close()
    if record is None:
        return None
    final = bundle_paths(library_dir, key)
    images = []
    try:
        import json

        images = json.loads(record.get("images") or "[]")
    except Exception:
        images = []
    record["image_paths"] = [str(final.root / image) for image in images if image]
    if record.get("crop_path"):
        record["crop_path"] = str(final.root / record["crop_path"])
    return record


def get_table(library_dir: Path, key: str, table_ref: str) -> dict[str, Any] | None:
    conn = connect(library_dir)
    try:
        record = db_module.get_table(conn, key, table_ref)
    finally:
        conn.close()
    if record is None:
        return None
    final = bundle_paths(library_dir, key)
    if record.get("html_path"):
        candidate = final.root / record["html_path"]
        record["html_path"] = str(candidate) if candidate.is_file() else None
    if record.get("csv_path"):
        candidate = final.root / record["csv_path"]
        record["csv_path"] = str(candidate) if candidate.is_file() else None
    if record.get("crop_path"):
        crop = final.root / record["crop_path"]
        record["crop_path"] = str(crop) if crop.is_file() else None

    if not record.get("html_path") and not record.get("csv_path"):
        fallback = _table_from_document(library_dir, key, table_ref)
        if fallback:
            record.update(fallback)
    return record


def _table_from_document(library_dir: Path, key: str, table_ref: str) -> dict[str, Any] | None:
    from .assets import render_table_html, table_to_csv
    from .io_utils import read_json

    document = read_json(bundle_paths(library_dir, key).document, default=None)
    if not isinstance(document, dict):
        return None
    needle = str(table_ref).strip().lower().rstrip(":").strip()
    for block in document.get("blocks") or []:
        if block.get("kind") != "table" or not isinstance(block.get("table"), dict):
            continue
        table = block["table"]
        candidates = {
            str(block.get("id", "")).strip().lower(),
            str(table.get("id", "")).strip().lower(),
            str(table.get("label", "")).strip().lower().rstrip(":").strip(),
        }
        if needle not in candidates:
            continue
        return {"html_text": render_table_html(table), "csv_text": table_to_csv(table)}
    if needle.isdigit():
        tables = [
            block["table"]
            for block in document.get("blocks") or []
            if block.get("kind") == "table" and isinstance(block.get("table"), dict)
        ]
        index = int(needle) - 1
        if 0 <= index < len(tables):
            table = tables[index]
            return {"html_text": render_table_html(table), "csv_text": table_to_csv(table)}
    return None


def annotate(library_dir: Path, key: str, quote: str, note: str) -> int:
    conn = connect(library_dir)
    try:
        return db_module.add_annotation(conn, key, quote, note)
    finally:
        conn.close()


def annotations(library_dir: Path, key: str) -> list[dict[str, Any]]:
    conn = connect(library_dir)
    try:
        return db_module.list_annotations(conn, key)
    finally:
        conn.close()


def stats(library_dir: Path) -> dict[str, Any]:
    conn = connect(library_dir)
    try:
        return db_module.stats(conn)
    finally:
        conn.close()
