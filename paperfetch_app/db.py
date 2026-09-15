from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .bundle import bundle_paths, load_bundle_meta
from .io_utils import now_utc_iso, read_json, safe_slug
from .storage import index_path, load_index

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS papers (
    key TEXT PRIMARY KEY,
    identity_kind TEXT,
    identity_value TEXT,
    fingerprint TEXT,
    title TEXT,
    abstract TEXT,
    year INTEGER,
    venue TEXT,
    doi TEXT,
    arxiv_id TEXT,
    license TEXT,
    source_url TEXT,
    extractor TEXT,
    source_kind TEXT,
    coverage_ratio REAL,
    coverage_ok INTEGER,
    status TEXT,
    error TEXT,
    bundle_path TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS authors (
    paper_key TEXT NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (paper_key, position)
);
CREATE TABLE IF NOT EXISTS sections (
    paper_key TEXT NOT NULL,
    section_id TEXT NOT NULL,
    parent_id TEXT,
    level INTEGER,
    title TEXT,
    ordinal INTEGER,
    start_block INTEGER,
    end_block INTEGER,
    char_count INTEGER,
    PRIMARY KEY (paper_key, section_id)
);
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key TEXT NOT NULL,
    section_id TEXT,
    ordinal INTEGER,
    kind TEXT,
    text TEXT
);
CREATE INDEX IF NOT EXISTS idx_blocks_paper ON blocks(paper_key, ordinal);
CREATE TABLE IF NOT EXISTS figures (
    id TEXT NOT NULL,
    paper_key TEXT NOT NULL,
    label TEXT,
    caption TEXT,
    images TEXT,
    pdf_page INTEGER,
    crop_path TEXT,
    PRIMARY KEY (paper_key, id)
);
CREATE TABLE IF NOT EXISTS tables_index (
    id TEXT NOT NULL,
    paper_key TEXT NOT NULL,
    label TEXT,
    caption TEXT,
    html_path TEXT,
    csv_path TEXT,
    pdf_page INTEGER,
    crop_path TEXT,
    rows INTEGER,
    cols INTEGER,
    PRIMARY KEY (paper_key, id)
);
CREATE TABLE IF NOT EXISTS equations (
    id TEXT NOT NULL,
    paper_key TEXT NOT NULL,
    label TEXT,
    tex TEXT,
    display INTEGER,
    number TEXT,
    PRIMARY KEY (paper_key, id)
);
CREATE TABLE IF NOT EXISTS refs (
    src_key TEXT NOT NULL,
    bibkey TEXT,
    label TEXT,
    raw TEXT,
    doi TEXT,
    arxiv_id TEXT,
    title TEXT,
    PRIMARY KEY (src_key, bibkey)
);
CREATE TABLE IF NOT EXISTS files (
    paper_key TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    sha256 TEXT,
    bytes INTEGER,
    media_type TEXT,
    PRIMARY KEY (paper_key, rel_path)
);
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key TEXT NOT NULL,
    quote TEXT,
    note TEXT,
    created_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    paper_key UNINDEXED,
    section_id UNINDEXED,
    ordinal UNINDEXED,
    text,
    tokenize = 'unicode61'
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def _plain_inlines(inlines: list[dict[str, Any]] | None) -> str:
    if not inlines:
        return ""
    parts: list[str] = []
    for inline in inlines:
        kind = inline.get("kind")
        if kind == "text":
            parts.append(str(inline.get("text") or ""))
        elif kind == "math":
            parts.append(f"${inline.get('tex')}$")
        elif kind == "cite":
            parts.append("[" + ", ".join(inline.get("keys") or []) + "]")
        elif inline.get("children"):
            parts.append(_plain_inlines(inline["children"]))
        else:
            parts.append(str(inline.get("text") or ""))
    return "".join(parts)


def _block_text(block: dict[str, Any]) -> str:
    kind = block.get("kind")
    if kind == "heading":
        return _plain_inlines(block.get("inlines"))
    if kind == "paragraph":
        return _plain_inlines(block.get("inlines"))
    if kind == "math":
        label = (block.get("meta") or {}).get("label")
        return f"${block.get('tex', '')}$" + (f" {label}" if label else "")
    if kind == "code":
        return str(block.get("text") or "")
    if kind == "list":
        lines = []
        for item in block.get("items", []):
            lines.append("- " + _plain_inlines(item.get("inlines")))
        return "\n".join(lines)
    if kind == "table":
        table = block.get("table") or {}
        return "Table: " + _plain_inlines(table.get("caption"))
    if kind == "figure":
        figure = block.get("figure") or {}
        return "Figure: " + _plain_inlines(figure.get("caption"))
    if kind == "footnote":
        return _plain_inlines(block.get("inlines"))
    return ""


def index_bundle(conn: sqlite3.Connection, library_dir: Path, key: str) -> bool:
    """Index one bundle into SQLite. Returns False when the bundle is missing."""
    paths = bundle_paths(library_dir, key)
    meta = load_bundle_meta(paths.root)
    if not meta:
        legacy = load_index(index_path(library_dir)).get(key)
        if not legacy:
            return False
        meta = dict(legacy)
        meta.setdefault("key", key)
        meta.setdefault("updated_at", now_utc_iso())

    document = read_json(paths.document, default=None)
    markdown_path = paths.markdown
    if not markdown_path.exists():
        md_rel = meta.get("md")
        if isinstance(md_rel, str):
            markdown_path = library_dir / md_rel
    markdown = markdown_path.read_text(encoding="utf-8", errors="replace") if markdown_path.exists() else ""
    coverage = meta.get("coverage") or {}
    if not isinstance(coverage, dict):
        coverage = {}

    conn.execute("DELETE FROM authors WHERE paper_key=?", (key,))
    conn.execute("DELETE FROM sections WHERE paper_key=?", (key,))
    conn.execute("DELETE FROM blocks WHERE paper_key=?", (key,))
    conn.execute("DELETE FROM figures WHERE paper_key=?", (key,))
    conn.execute("DELETE FROM tables_index WHERE paper_key=?", (key,))
    conn.execute("DELETE FROM equations WHERE paper_key=?", (key,))
    conn.execute("DELETE FROM refs WHERE src_key=?", (key,))
    conn.execute("DELETE FROM files WHERE paper_key=?", (key,))
    conn.execute("DELETE FROM chunks_fts WHERE paper_key=?", (key,))

    conn.execute(
        """
        INSERT INTO papers(key, identity_kind, identity_value, fingerprint, title, abstract, year, venue,
                           doi, arxiv_id, license, source_url, extractor, source_kind, coverage_ratio,
                           coverage_ok, status, error, bundle_path, created_at, updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
            identity_kind=excluded.identity_kind, identity_value=excluded.identity_value,
            fingerprint=excluded.fingerprint, title=excluded.title, abstract=excluded.abstract,
            year=excluded.year, venue=excluded.venue, doi=excluded.doi, arxiv_id=excluded.arxiv_id,
            license=excluded.license, source_url=excluded.source_url, extractor=excluded.extractor,
            source_kind=excluded.source_kind, coverage_ratio=excluded.coverage_ratio,
            coverage_ok=excluded.coverage_ok, status=excluded.status, error=excluded.error,
            bundle_path=excluded.bundle_path, updated_at=excluded.updated_at
        """,
        (
            key,
            meta.get("identity_kind"),
            meta.get("identity_value"),
            meta.get("identity_fingerprint"),
            meta.get("title"),
            meta.get("abstract"),
            meta.get("year"),
            meta.get("venue"),
            meta.get("doi"),
            meta.get("arxiv_id"),
            meta.get("license"),
            meta.get("source_url"),
            meta.get("extractor"),
            meta.get("source_kind"),
            coverage.get("ratio"),
            1 if coverage.get("ok") else 0,
            "indexed",
            None,
            str(paths.root.relative_to(library_dir)),
            meta.get("created_at") or meta.get("updated_at") or now_utc_iso(),
            meta.get("updated_at") or now_utc_iso(),
        ),
    )

    authors = meta.get("authors") or []
    if isinstance(authors, list):
        for position, name in enumerate(authors):
            conn.execute(
                "INSERT OR REPLACE INTO authors(paper_key, position, name) VALUES(?,?,?)",
                (key, position, str(name)),
            )

    if isinstance(document, dict) and document.get("blocks"):
        _index_document(conn, key, document)
    else:
        _index_markdown(conn, key, markdown)

    bibliography = (document or {}).get("bibliography") if isinstance(document, dict) else None
    if isinstance(bibliography, list):
        for entry in bibliography:
            if not isinstance(entry, dict):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO refs(src_key, bibkey, label, raw, doi, arxiv_id, title) VALUES(?,?,?,?,?,?,?)",
                (
                    key,
                    entry.get("id"),
                    entry.get("label"),
                    entry.get("raw_text"),
                    None,
                    None,
                    None,
                ),
            )

    for file_path in sorted(paths.root.rglob("*")):
        if file_path.is_file() and file_path != paths.checksums:
            rel = str(file_path.relative_to(paths.root))
            conn.execute(
                "INSERT OR REPLACE INTO files(paper_key, rel_path, sha256, bytes, media_type) VALUES(?,?,?,?,?)",
                (key, rel, None, file_path.stat().st_size, None),
            )

    conn.commit()
    return True


def _index_document(conn: sqlite3.Connection, key: str, document: dict[str, Any]) -> None:
    blocks = document.get("blocks") or []
    section_stack: list[tuple[int, str]] = []
    sections: dict[str, dict[str, Any]] = {}
    current_section: str | None = None

    for ordinal, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        kind = block.get("kind")
        text = _block_text(block)
        if kind == "heading":
            level = int(block.get("level") or 1)
            section_id = str(block.get("id") or f"sec-{ordinal}")
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            parent = section_stack[-1][1] if section_stack else None
            section_stack.append((level, section_id))
            sections[section_id] = {
                "id": section_id,
                "parent": parent,
                "level": level,
                "title": text,
                "ordinal": ordinal,
                "start": ordinal,
                "end": ordinal,
                "chars": 0,
            }
            current_section = section_id
        if current_section and current_section in sections:
            sections[current_section]["end"] = ordinal
            sections[current_section]["chars"] += len(text)
        if text:
            conn.execute(
                "INSERT INTO blocks(paper_key, section_id, ordinal, kind, text) VALUES(?,?,?,?,?)",
                (key, current_section, ordinal, kind, text),
            )
            conn.execute(
                "INSERT INTO chunks_fts(paper_key, section_id, ordinal, text) VALUES(?,?,?,?)",
                (key, current_section, ordinal, text),
            )

        if kind == "figure" and isinstance(block.get("figure"), dict):
            figure = block["figure"]
            conn.execute(
                "INSERT OR REPLACE INTO figures(id, paper_key, label, caption, images, pdf_page, crop_path) VALUES(?,?,?,?,?,?,?)",
                (
                    str(block.get("id") or figure.get("id")),
                    key,
                    figure.get("label"),
                    _plain_inlines(figure.get("caption")),
                    json.dumps([img.get("local") or img.get("src") for img in figure.get("images", [])]),
                    (block.get("meta") or {}).get("pdf_page"),
                    (block.get("meta") or {}).get("pdf_crop"),
                ),
            )
        if kind == "table" and isinstance(block.get("table"), dict):
            table = block["table"]
            rows = table.get("rows") or []
            cols = max((len(row) for row in rows), default=0)
            conn.execute(
                "INSERT OR REPLACE INTO tables_index(id, paper_key, label, caption, html_path, csv_path, pdf_page, crop_path, rows, cols) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(block.get("id") or table.get("id")),
                    key,
                    table.get("label"),
                    _plain_inlines(table.get("caption")),
                    f"tables/{safe_slug(str(table.get('id')), max_length=60)}.html",
                    f"tables/{safe_slug(str(table.get('id')), max_length=60)}.csv",
                    (block.get("meta") or {}).get("pdf_page"),
                    (block.get("meta") or {}).get("pdf_crop"),
                    len(rows),
                    cols,
                ),
            )
        if kind == "math":
            meta = block.get("meta") or {}
            conn.execute(
                "INSERT OR REPLACE INTO equations(id, paper_key, label, tex, display, number) VALUES(?,?,?,?,?,?)",
                (
                    str(block.get("id") or f"eq-{ordinal}"),
                    key,
                    meta.get("label"),
                    block.get("tex"),
                    1 if meta.get("display", True) else 0,
                    meta.get("label"),
                ),
            )

    for section in sections.values():
        conn.execute(
            """INSERT OR REPLACE INTO sections(paper_key, section_id, parent_id, level, title, ordinal,
               start_block, end_block, char_count) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                key,
                section["id"],
                section["parent"],
                section["level"],
                section["title"],
                section["ordinal"],
                section["start"],
                section["end"],
                section["chars"],
            ),
        )


def _index_markdown(conn: sqlite3.Connection, key: str, markdown: str) -> None:
    """Fallback indexing for output without an IR document."""
    lines = markdown.splitlines()
    current_id: str | None = None
    buffer: list[str] = []
    section_ordinal = 0
    block_ordinal = 0

    def flush() -> None:
        nonlocal buffer, block_ordinal
        text = "\n".join(buffer).strip()
        if text:
            conn.execute(
                "INSERT INTO blocks(paper_key, section_id, ordinal, kind, text) VALUES(?,?,?,?,?)",
                (key, current_id, block_ordinal, "paragraph", text),
            )
            conn.execute(
                "INSERT INTO chunks_fts(paper_key, section_id, ordinal, text) VALUES(?,?,?,?)",
                (key, current_id, block_ordinal, text),
            )
            if current_id:
                conn.execute(
                    "UPDATE sections SET char_count = char_count + ? WHERE paper_key=? AND section_id=?",
                    (len(text), key, current_id),
                )
        buffer = []
        block_ordinal += 1

    for line in lines:
        if line.startswith("#"):
            flush()
            section_ordinal += 1
            title = line.lstrip("#").strip()
            current_id = f"md-sec-{section_ordinal}"
            conn.execute(
                """INSERT OR REPLACE INTO sections(paper_key, section_id, parent_id, level, title, ordinal,
                   start_block, end_block, char_count) VALUES(?,?,?,?,?,?,?,?,?)""",
                (key, current_id, None, max(1, len(line) - len(line.lstrip("#"))), title, section_ordinal, 0, 0, 0),
            )
        else:
            buffer.append(line)
    flush()


def rebuild(conn: sqlite3.Connection, library_dir: Path) -> dict[str, int]:
    indexed = 0
    for child in sorted(library_dir.iterdir()) if library_dir.exists() else []:
        if child.is_dir() and not child.name.startswith(".") and (child / "meta.json").exists():
            if index_bundle(conn, library_dir, child.name):
                indexed += 1
    legacy = load_index(index_path(library_dir))
    for key in legacy:
        if not (library_dir / key).exists() and index_bundle(conn, library_dir, key):
            indexed += 1
    conn.commit()
    return {"indexed": indexed}


def search(conn: sqlite3.Connection, query: str, *, paper_key: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    sql = (
        "SELECT paper_key, section_id, ordinal, "
        "snippet(chunks_fts, 3, '[', ']', ' ... ', 12) AS snippet, "
        "bm25(chunks_fts) AS score "
        "FROM chunks_fts WHERE chunks_fts MATCH ?"
    )
    params: list[Any] = [query]
    if paper_key:
        sql += " AND paper_key = ?"
        params.append(paper_key)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def outline(conn: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT section_id, parent_id, level, title, ordinal, char_count FROM sections WHERE paper_key=? ORDER BY ordinal",
        (key,),
    ).fetchall()
    return [dict(row) for row in rows]


def read_section(conn: sqlite3.Connection, key: str, section_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ordinal, kind, text FROM blocks WHERE paper_key=? AND section_id=? ORDER BY ordinal",
        (key, section_id),
    ).fetchall()
    return [dict(row) for row in rows]


def get_paper(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM papers WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    paper = dict(row)
    paper["authors"] = [
        item["name"]
        for item in conn.execute(
            "SELECT name FROM authors WHERE paper_key=? ORDER BY position", (key,)
        ).fetchall()
    ]
    return paper


def list_papers(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    where = ""
    params: list[Any] = []
    if query:
        where = " WHERE title LIKE ? OR abstract LIKE ?"
        params.extend([f"%{query}%", f"%{query}%"])
    total = conn.execute(f"SELECT COUNT(*) FROM papers{where}", params).fetchone()[0]
    sql = f"SELECT * FROM papers{where} ORDER BY updated_at DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return int(total), [dict(row) for row in rows]


def _norm_label(value: Any) -> str:
    import re

    return re.sub(r"\s*:\s*$", "", str(value or "").strip()).lower()


def get_figure(conn: sqlite3.Connection, key: str, figure_ref: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM figures WHERE paper_key=?", (key,)).fetchall()
    needle = _norm_label(figure_ref)
    for item in row:
        candidate = dict(item)
        if needle in {_norm_label(candidate.get("id")), _norm_label(candidate.get("label"))}:
            return candidate
    if needle.isdigit() and row:
        index = int(needle) - 1
        if 0 <= index < len(row):
            return dict(row[index])
    return None


def get_table(conn: sqlite3.Connection, key: str, table_ref: str) -> dict[str, Any] | None:
    rows = conn.execute("SELECT * FROM tables_index WHERE paper_key=?", (key,)).fetchall()
    needle = _norm_label(table_ref)
    for item in rows:
        candidate = dict(item)
        if needle in {_norm_label(candidate.get("id")), _norm_label(candidate.get("label"))}:
            return candidate
    if needle.isdigit() and rows:
        index = int(needle) - 1
        if 0 <= index < len(rows):
            return dict(rows[index])
    return None


def add_annotation(conn: sqlite3.Connection, key: str, quote: str, note: str) -> int:
    cursor = conn.execute(
        "INSERT INTO annotations(paper_key, quote, note, created_at) VALUES(?,?,?,?)",
        (key, quote, note, now_utc_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def list_annotations(conn: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, quote, note, created_at FROM annotations WHERE paper_key=? ORDER BY id", (key,)
    ).fetchall()
    return [dict(row) for row in rows]


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    extractors = {
        row["extractor"] or "unknown": row["count"]
        for row in conn.execute("SELECT extractor, COUNT(*) AS count FROM papers GROUP BY extractor")
    }
    years = {
        int(row["year"]): row["count"]
        for row in conn.execute("SELECT year, COUNT(*) AS count FROM papers WHERE year IS NOT NULL GROUP BY year")
    }
    return {
        "total_papers": int(total),
        "by_extractor": extractors,
        "by_year": dict(sorted(years.items())),
        "figures": conn.execute("SELECT COUNT(*) FROM figures").fetchone()[0],
        "tables": conn.execute("SELECT COUNT(*) FROM tables_index").fetchone()[0],
        "equations": conn.execute("SELECT COUNT(*) FROM equations").fetchone()[0],
        "references": conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0],
    }
