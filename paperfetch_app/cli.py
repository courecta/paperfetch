from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bibtex import export_bibtex
from .config import VERSION, get_default_library_dir, get_default_marker_venv
from .discover import format_discovered, search_semantic_scholar
from .identity import build_identity
from .io_utils import ensure_dir, read_json
from .locking import file_lock
from .manifests import collect_inputs, init_manifest
from .models import FetchOptions
from .pipeline import reextract_keys, run_fetch
from .storage import clean_library, index_path, list_entries, load_index, save_index

try:
    from .server import create_app

    _HAS_FASTAPI = True
except Exception:
    _HAS_FASTAPI = False

try:
    from .mcp_server import run_mcp_server

    _HAS_MCP = True
except Exception:
    _HAS_MCP = False

COMMANDS = {
    "fetch",
    "list",
    "inspect",
    "reextract",
    "clean",
    "export",
    "discover",
    "serve",
    "mcp",
    "migrate",
    "install-mcp",
}
DEFAULT_LIBRARY_DIR = get_default_library_dir()
DEFAULT_MARKER_VENV = get_default_marker_venv()


def _add_common_io_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--out-dir", type=Path, default=Path.cwd() / "papers")
    parser.add_argument("--project-files", choices=["md", "both", "none", "figures"], default="md")
    parser.add_argument("--link-mode", choices=["copy", "symlink", "hardlink"], default="copy")
    parser.add_argument("--overwrite", action="store_true")


def _add_extraction_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--extractor", choices=["auto", "arxiv_html", "marker", "pymupdf"], default="auto")
    parser.add_argument("--marker-venv", type=Path, default=DEFAULT_MARKER_VENV)
    parser.add_argument("--no-install-marker", action="store_true")
    parser.add_argument("--prefer-pymupdf", action="store_true")
    parser.add_argument("--min-coverage", type=float, default=0.95)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--no-figures", action="store_true")
    parser.add_argument("--no-pdf-visual", action="store_true")
    parser.add_argument("--max-asset-mb", type=int, default=25)
    parser.add_argument("--pdf", type=Path, help="Use a local PDF instead of downloading")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperfetch",
        description="Paper ingestion with lossless bundles for research agents.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch/extract papers into the library.")
    fetch.add_argument("--manifest", action="append", default=[])
    fetch.add_argument("--from-python", action="append", default=[])
    fetch.add_argument("--paper", action="append", default=[])
    fetch.add_argument("--url", action="append", default=[])
    fetch.add_argument("--download-timeout", type=int, default=120)
    fetch.add_argument("--marker-timeout", type=int, default=900)
    fetch.add_argument("--download-retries", type=int, default=3)
    fetch.add_argument("--marker-retries", type=int, default=1)
    fetch.add_argument("--download-backoff", type=float, default=1.5)
    fetch.add_argument("--marker-backoff", type=float, default=2.0)
    fetch.add_argument("--workers", type=int, default=4)
    fetch.add_argument("--min-md-chars", type=int, default=300)
    fetch.add_argument("--min-md-lines", type=int, default=8)
    fetch.add_argument("--allow-low-quality-md", action="store_true")
    fetch.add_argument("--force-download", action="store_true")
    fetch.add_argument("--refresh-md", action="store_true")
    fetch.add_argument("--dry-run", action="store_true")
    fetch.add_argument("--resolve-only", action="store_true", help="Print the resolution plan without downloading")
    fetch.add_argument("--init-manifest", type=Path)
    fetch.add_argument("--verbose", action="store_true")
    _add_extraction_options(fetch)
    _add_common_io_options(fetch)

    list_cmd = subparsers.add_parser("list", help="List library entries.")
    list_cmd.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    list_cmd.add_argument("--limit", type=int, default=50)
    list_cmd.add_argument("--json", action="store_true", dest="as_json")

    inspect = subparsers.add_parser("inspect", help="Inspect one bundle.")
    inspect.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    inspect.add_argument("--key")
    inspect.add_argument("--url")
    inspect.add_argument("--document", action="store_true", help="Print document.json")

    reextract = subparsers.add_parser("reextract", help="Re-run extraction for cached entries.")
    reextract.add_argument("--key", action="append", default=[])
    reextract.add_argument("--url", action="append", default=[])
    reextract.add_argument("--all", action="store_true")
    reextract.add_argument("--marker-timeout", type=int, default=900)
    reextract.add_argument("--marker-retries", type=int, default=1)
    reextract.add_argument("--marker-backoff", type=float, default=2.0)
    reextract.add_argument("--workers", type=int, default=4)
    reextract.add_argument("--dry-run", action="store_true")
    reextract.add_argument("--verbose", action="store_true")
    reextract.add_argument("--download-timeout", type=int, default=120)
    reextract.add_argument("--download-retries", type=int, default=3)
    reextract.add_argument("--download-backoff", type=float, default=1.5)
    reextract.add_argument("--force-download", action="store_true")
    reextract.add_argument("--refresh-md", action="store_true", default=True)
    _add_extraction_options(reextract)
    _add_common_io_options(reextract)

    clean = subparsers.add_parser("clean", help="Clean stale entries/orphans.")
    clean.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    clean.add_argument("--prune-missing-entries", action="store_true")
    clean.add_argument("--remove-orphans", action="store_true")

    export_cmd = subparsers.add_parser("export", help="Export library entries to BibTeX.")
    export_cmd.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    export_cmd.add_argument("--key", action="append", default=[])
    export_cmd.add_argument("--url", action="append", default=[])
    export_cmd.add_argument("--all", action="store_true")
    export_cmd.add_argument("--format", choices=["bibtex"], default="bibtex")
    export_cmd.add_argument("-o", "--output", type=Path)

    discover_cmd = subparsers.add_parser("discover", help="Search for papers via Semantic Scholar.")
    discover_cmd.add_argument("query", nargs="+", help="Search query")
    discover_cmd.add_argument("--limit", type=int, default=20)
    discover_cmd.add_argument("--year", help="Year filter, e.g., 2023 or 2020-2025")
    discover_cmd.add_argument("--open-access", action="store_true")
    discover_cmd.add_argument("--venue")
    discover_cmd.add_argument("--format", choices=["urls", "tsv", "json", "manifest"], default="urls")
    discover_cmd.add_argument("-o", "--output", type=Path)

    serve_cmd = subparsers.add_parser("serve", help="Start HTTP API server.")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765)
    serve_cmd.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    serve_cmd.add_argument("--marker-venv", type=Path, default=DEFAULT_MARKER_VENV)

    mcp_cmd = subparsers.add_parser("mcp", help="Start MCP server over stdio.")
    mcp_cmd.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    mcp_cmd.add_argument("--marker-venv", type=Path, default=DEFAULT_MARKER_VENV)

    migrate_cmd = subparsers.add_parser("migrate", help="Migrate legacy flat libraries to bundles.")
    migrate_cmd.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    migrate_cmd.add_argument("--delete-old", action="store_true")

    install_cmd = subparsers.add_parser("install-mcp", help="Install the paperfetch MCP server into opencode.")
    install_cmd.add_argument("--client", choices=["opencode"], default="opencode")
    install_cmd.add_argument("--scope", choices=["project", "global"], default="project")
    install_cmd.add_argument("--project-dir", type=Path, default=Path.cwd())
    install_cmd.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    install_cmd.add_argument("--marker-venv", type=Path, default=DEFAULT_MARKER_VENV)
    install_cmd.add_argument("--no-skill", action="store_true")
    install_cmd.add_argument("--force", action="store_true")

    return parser


def _to_fetch_options(args: argparse.Namespace) -> FetchOptions:
    out_dir = args.out_dir.expanduser().resolve() if getattr(args, "out_dir", None) else None
    library_dir = args.library_dir.expanduser().resolve()
    ensure_dir(library_dir)
    if out_dir:
        ensure_dir(out_dir)

    return FetchOptions(
        library_dir=library_dir,
        out_dir=out_dir,
        project_files=getattr(args, "project_files", "none"),
        link_mode=getattr(args, "link_mode", "copy"),
        overwrite=getattr(args, "overwrite", False),
        force_download=getattr(args, "force_download", False),
        refresh_md=getattr(args, "refresh_md", False),
        dry_run=getattr(args, "dry_run", False),
        download_timeout=getattr(args, "download_timeout", 120),
        marker_timeout=getattr(args, "marker_timeout", 900),
        download_retries=max(0, int(getattr(args, "download_retries", 3))),
        marker_retries=max(0, int(getattr(args, "marker_retries", 1))),
        download_backoff=max(0.0, float(getattr(args, "download_backoff", 1.5))),
        marker_backoff=max(0.0, float(getattr(args, "marker_backoff", 2.0))),
        workers=max(1, int(getattr(args, "workers", 4))),
        min_md_chars=max(0, int(getattr(args, "min_md_chars", 300))),
        min_md_lines=max(0, int(getattr(args, "min_md_lines", 8))),
        allow_low_quality_md=getattr(args, "allow_low_quality_md", False),
        verbose=getattr(args, "verbose", False),
        extractor=getattr(args, "extractor", "auto"),
        min_coverage=float(getattr(args, "min_coverage", 0.95)),
        allow_incomplete=getattr(args, "allow_incomplete", False),
        download_figures=not getattr(args, "no_figures", False),
        pdf_visual=not getattr(args, "no_pdf_visual", False),
        max_asset_bytes=max(1, int(getattr(args, "max_asset_mb", 25))) * 1024 * 1024,
        marker_venv=getattr(args, "marker_venv", DEFAULT_MARKER_VENV).expanduser().resolve()
        if getattr(args, "marker_venv", None)
        else None,
        install_marker=not getattr(args, "no_install_marker", False),
        prefer_pymupdf=getattr(args, "prefer_pymupdf", False),
        pdf_path=getattr(args, "pdf", None),
    )


def _load_index_with_lock(library_dir: Path) -> dict[str, dict]:
    idx = index_path(library_dir)
    lock = library_dir / "index.lock"
    with file_lock(lock, timeout_sec=120.0):
        return load_index(idx)


def _save_index_with_lock(library_dir: Path, updated_index: dict[str, dict]) -> None:
    idx = index_path(library_dir)
    lock = library_dir / "index.lock"
    with file_lock(lock, timeout_sec=120.0):
        current = load_index(idx)
        current.update(updated_index)
        save_index(idx, current)


def _run_fetch(args: argparse.Namespace) -> int:
    if args.init_manifest:
        init_manifest(args.init_manifest.expanduser().resolve())
        print(f"Wrote manifest template: {args.init_manifest}")
        return 0

    has_inputs = bool(args.manifest or args.from_python or args.paper or args.url)
    if not has_inputs:
        print("Usage: paperfetch fetch [OPTIONS] [--url URL ...] [--manifest PATH ...]")
        print("\nCommon examples:")
        print("  paperfetch fetch --url https://arxiv.org/abs/2501.00001")
        print("  paperfetch fetch --manifest papers.csv")
        print("  paperfetch fetch --url <url1> --url <url2> --out-dir ./project")
        print("\nRun 'paperfetch fetch --help' for all options.")
        return 0

    papers = collect_inputs(args.manifest, args.from_python, args.paper, args.url)

    if args.resolve_only:
        from .fetch.http import HttpClient
        from .resolve.base import resolve

        client = HttpClient(timeout=30, retries=1)
        try:
            for paper in papers:
                resolution = resolve(build_identity(paper.url), client)
                print(json.dumps(resolution.to_dict(), indent=2))
        finally:
            client.close()
        return 0

    options = _to_fetch_options(args)
    snapshot = _load_index_with_lock(options.library_dir)
    results = run_fetch(papers, options, None, snapshot)

    failures = 0
    updates: dict[str, dict] = {}
    for result in results:
        if result.success:
            if result.dry_run:
                print(f"[dry-run] {result.paper.title or result.paper.url}")
            else:
                title = result.paper.title or (result.index_entry or {}).get("title") or result.paper.url
                coverage = result.coverage or {}
                ratio = coverage.get("ratio")
                suffix = f" coverage={ratio}" if ratio is not None else ""
                print(f"[ok] {title}{suffix}")
                if result.index_entry is not None:
                    updates[result.key] = result.index_entry
        else:
            failures += 1
            print(f"[fail] {result.paper.title or result.paper.url}: {result.error}")

    if updates and not options.dry_run:
        _save_index_with_lock(options.library_dir, updates)

    print(f"Processed {len(results)} papers, failures={failures}")
    return 1 if failures else 0


def _run_list(args: argparse.Namespace) -> int:
    library_dir = args.library_dir.expanduser().resolve()
    rows = list_entries(load_index(index_path(library_dir)), limit=args.limit)
    if args.as_json:
        payload = [{"key": key, **entry} for key, entry in rows]
        print(json.dumps(payload, indent=2))
        return 0

    if not rows:
        print("No library entries.")
        return 0

    for key, entry in rows:
        title = str(entry.get("title", ""))
        kind = str(entry.get("identity_kind", ""))
        value = str(entry.get("identity_value", ""))
        extractor = str(entry.get("extractor", ""))
        updated = str(entry.get("updated_at", ""))
        print(f"{key[:10]}  {kind}:{value}  [{extractor}]  {title}  {updated}")
    return 0


def _resolve_key_from_args(index: dict[str, dict], key: str | None, url: str | None) -> str | None:
    if key:
        return key
    if url:
        return build_identity(url).key
    return None


def _run_inspect(args: argparse.Namespace) -> int:
    library_dir = args.library_dir.expanduser().resolve()
    idx = load_index(index_path(library_dir))
    key = _resolve_key_from_args(idx, args.key, args.url)
    if not key:
        print("Provide --key or --url")
        return 1

    entry = idx.get(key)
    if not entry:
        print("Entry not found")
        return 1

    print(json.dumps({"key": key, **entry}, indent=2))
    bundle_meta = library_dir / key / "meta.json"
    if bundle_meta.exists():
        print(bundle_meta.read_text(encoding="utf-8"))
    if args.document:
        document = library_dir / key / "document.json"
        if document.exists():
            payload = read_json(document, default={})
            coverage = payload.get("coverage", {}) if isinstance(payload, dict) else {}
            print(json.dumps({"coverage": coverage}, indent=2))
    return 0


def _run_reextract(args: argparse.Namespace) -> int:
    options = _to_fetch_options(args)
    idx_snapshot = _load_index_with_lock(options.library_dir)

    keys: set[str] = set(args.key)
    for url in args.url:
        keys.add(build_identity(url).key)
    if args.all:
        keys.update(idx_snapshot.keys())

    if not keys:
        print("No targets selected. Use --key, --url, or --all.")
        return 1

    results = reextract_keys(sorted(keys), options, None, idx_snapshot)
    failures = 0
    updates: dict[str, dict] = {}
    for result in results:
        if result.success:
            title = result.paper.title or (result.index_entry or {}).get("title") or result.paper.url
            print(f"[ok] {title}")
            if result.index_entry is not None:
                updates[result.key] = result.index_entry
        else:
            failures += 1
            print(f"[fail] {result.paper.title or result.paper.url}: {result.error}")

    if updates and not options.dry_run:
        _save_index_with_lock(options.library_dir, updates)

    print(f"Reextracted {len(results)} papers, failures={failures}")
    return 1 if failures else 0


def _run_export(args: argparse.Namespace) -> int:
    library_dir = args.library_dir.expanduser().resolve()
    idx = load_index(index_path(library_dir))

    keys: set[str] = set(args.key)
    for url in args.url:
        keys.add(build_identity(url).key)
    if args.all:
        keys.update(idx.keys())

    if not keys:
        print("No targets selected. Use --key, --url, or --all.")
        return 1

    entries = [idx[key] for key in keys if key in idx]
    if not entries:
        print("No matching entries found.")
        return 1

    output = export_bibtex(entries)
    if args.output:
        ensure_dir(args.output.parent)
        args.output.write_text(output, encoding="utf-8")
        print(f"Exported {len(entries)} entries to {args.output}")
    else:
        print(output)
    return 0


def _run_discover(args: argparse.Namespace) -> int:
    query = " ".join(args.query)
    year_start: int | None = None
    year_end: int | None = None
    if args.year:
        parts = args.year.split("-")
        if len(parts) == 1:
            year_start = year_end = int(parts[0])
        elif len(parts) == 2:
            year_start = int(parts[0]) if parts[0] else None
            year_end = int(parts[1]) if parts[1] else None

    try:
        papers = search_semantic_scholar(
            query=query,
            limit=args.limit,
            year_start=year_start,
            year_end=year_end,
            open_access_only=args.open_access,
            venue=args.venue,
        )
    except RuntimeError as exc:
        err = str(exc)
        if "429" in err:
            print("Semantic Scholar rate limit exceeded. Set S2_API_KEY or wait and retry.")
            return 1
        raise

    if not papers:
        print("No papers found.")
        return 0

    output = format_discovered(papers, args.format)
    if args.output:
        ensure_dir(args.output.parent)
        args.output.write_text(output, encoding="utf-8")
        print(f"Found {len(papers)} papers. Wrote {args.output}")
    else:
        print(output)
    return 0


def _run_clean(args: argparse.Namespace) -> int:
    library_dir = args.library_dir.expanduser().resolve()
    idx_path = index_path(library_dir)
    lock = library_dir / "index.lock"

    with file_lock(lock, timeout_sec=120.0):
        idx = load_index(idx_path)
        summary = clean_library(
            library_dir=library_dir,
            index=idx,
            prune_missing_entries=args.prune_missing_entries,
            remove_orphans=args.remove_orphans,
        )
        save_index(idx_path, idx)

    print(json.dumps(summary, indent=2))
    return 0


def _run_migrate(args: argparse.Namespace) -> int:
    from .migrate import migrate_library

    library_dir = args.library_dir.expanduser().resolve()
    summary = migrate_library(library_dir, delete_old=args.delete_old)
    print(json.dumps(summary, indent=2))
    return 0


def _run_install_mcp(args: argparse.Namespace) -> int:
    from .opencode import install_mcp

    try:
        summary = install_mcp(
            scope=args.scope,
            project_dir=args.project_dir,
            library_dir=args.library_dir,
            marker_venv=args.marker_venv,
            install_skill=not args.no_skill,
            force=args.force,
        )
    except ValueError as exc:
        print(str(exc))
        return 1
    print(json.dumps(summary, indent=2))
    print("Restart opencode for the new MCP server to load.")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    if not _HAS_FASTAPI:
        print("FastAPI/uvicorn not installed. Install with: pip install paperfetch-tool[serve]")
        return 1

    import uvicorn

    app = create_app(library_dir=args.library_dir, marker_venv=args.marker_venv)
    print(f"Starting paperfetch API server at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _run_mcp(args: argparse.Namespace) -> int:
    if not _HAS_MCP:
        print("MCP server module not available.", file=sys.stderr)
        return 1
    run_mcp_server(library_dir=args.library_dir, marker_venv=args.marker_venv)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    parser = _build_parser()

    if not argv:
        parser.print_help()
        return 0

    if argv[0] not in COMMANDS and argv[0] not in {"-h", "--help", "--version", "-v"}:
        argv = ["fetch", *argv]

    args = parser.parse_args(argv)

    dispatch = {
        "fetch": _run_fetch,
        "list": _run_list,
        "inspect": _run_inspect,
        "reextract": _run_reextract,
        "clean": _run_clean,
        "export": _run_export,
        "discover": _run_discover,
        "serve": _run_serve,
        "mcp": _run_mcp,
        "migrate": _run_migrate,
        "install-mcp": _run_install_mcp,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.error(f"Unknown command: {args.command}")
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
