from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bibtex import export_bibtex
from .discover import format_discovered, search_semantic_scholar
from .identity import build_identity
from .locking import file_lock
from .manifests import collect_inputs, init_manifest
from .marker_backend import ensure_marker_command
from .models import FetchOptions
from .pipeline import reextract_keys, run_fetch
from .storage import clean_library, index_path, list_entries, load_index, save_index
from .io_utils import ensure_dir

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


from .config import get_default_library_dir, get_default_marker_venv

COMMANDS = {"fetch", "list", "inspect", "reextract", "clean", "export", "discover", "serve", "mcp"}
DEFAULT_LIBRARY_DIR = get_default_library_dir()
DEFAULT_MARKER_VENV = get_default_marker_venv()


def _add_common_io_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--out-dir", type=Path, default=Path.cwd() / "papers")
    parser.add_argument("--project-files", choices=["md", "both", "none"], default="md")
    parser.add_argument("--link-mode", choices=["copy", "symlink", "hardlink"], default="copy")
    parser.add_argument("--overwrite", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperfetch",
        description="Centralized paper ingestion with shared cache and marker extraction.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.3.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch/extract papers into cache and project output.")
    fetch.add_argument("--manifest", action="append", default=[])
    fetch.add_argument("--from-python", action="append", default=[])
    fetch.add_argument("--paper", action="append", default=[])
    fetch.add_argument("--url", action="append", default=[])
    fetch.add_argument("--extractor", choices=["auto", "marker", "arxiv_html"], default="auto")
    fetch.add_argument("--marker-venv", type=Path, default=DEFAULT_MARKER_VENV)
    fetch.add_argument("--no-install-marker", action="store_true")
    fetch.add_argument("--download-timeout", type=int, default=120)
    fetch.add_argument("--marker-timeout", type=int, default=900)
    fetch.add_argument("--download-retries", type=int, default=2)
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
    fetch.add_argument("--init-manifest", type=Path)
    fetch.add_argument("--verbose", action="store_true")
    _add_common_io_options(fetch)

    list_cmd = subparsers.add_parser("list", help="List cache entries.")
    list_cmd.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    list_cmd.add_argument("--limit", type=int, default=50)
    list_cmd.add_argument("--json", action="store_true", dest="as_json")

    inspect = subparsers.add_parser("inspect", help="Inspect one cache entry.")
    inspect.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    inspect.add_argument("--key")
    inspect.add_argument("--url")

    reextract = subparsers.add_parser("reextract", help="Re-run extraction for cached entries.")
    reextract.add_argument("--key", action="append", default=[])
    reextract.add_argument("--url", action="append", default=[])
    reextract.add_argument("--all", action="store_true")
    reextract.add_argument("--extractor", choices=["auto", "marker", "arxiv_html"], default="auto")
    reextract.add_argument("--marker-venv", type=Path, default=DEFAULT_MARKER_VENV)
    reextract.add_argument("--no-install-marker", action="store_true")
    reextract.add_argument("--marker-timeout", type=int, default=900)
    reextract.add_argument("--marker-retries", type=int, default=1)
    reextract.add_argument("--marker-backoff", type=float, default=2.0)
    reextract.add_argument("--workers", type=int, default=4)
    reextract.add_argument("--min-md-chars", type=int, default=300)
    reextract.add_argument("--min-md-lines", type=int, default=8)
    reextract.add_argument("--allow-low-quality-md", action="store_true")
    reextract.add_argument("--dry-run", action="store_true")
    reextract.add_argument("--verbose", action="store_true")
    reextract.add_argument("--download-timeout", type=int, default=120)
    reextract.add_argument("--download-retries", type=int, default=0)
    reextract.add_argument("--download-backoff", type=float, default=1.0)
    reextract.add_argument("--force-download", action="store_true")
    reextract.add_argument("--refresh-md", action="store_true")
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

    return parser


def _to_fetch_options(args: argparse.Namespace) -> FetchOptions:
    out_dir = args.out_dir.expanduser().resolve() if args.out_dir else None
    library_dir = args.library_dir.expanduser().resolve()
    ensure_dir(library_dir)
    if out_dir:
        ensure_dir(out_dir)

    return FetchOptions(
        library_dir=library_dir,
        out_dir=out_dir,
        project_files=args.project_files,
        link_mode=args.link_mode,
        overwrite=args.overwrite,
        force_download=args.force_download,
        refresh_md=args.refresh_md,
        dry_run=args.dry_run,
        download_timeout=args.download_timeout,
        marker_timeout=args.marker_timeout,
        download_retries=args.download_retries,
        marker_retries=args.marker_retries,
        download_backoff=args.download_backoff,
        marker_backoff=args.marker_backoff,
        workers=max(1, int(args.workers)),
        min_md_chars=max(0, int(args.min_md_chars)),
        min_md_lines=max(0, int(args.min_md_lines)),
        allow_low_quality_md=args.allow_low_quality_md,
        verbose=args.verbose,
        extractor=getattr(args, "extractor", "auto"),
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

    papers = collect_inputs(args.manifest, args.from_python, args.paper, args.url)
    options = _to_fetch_options(args)

    marker_cmd: str | None = None
    if not options.dry_run and options.extractor != "arxiv_html":
        marker_cmd = ensure_marker_command(
            marker_venv=args.marker_venv.expanduser().resolve(),
            allow_install=not args.no_install_marker,
            verbose=args.verbose,
        )
        if marker_cmd is None and options.extractor == "marker":
            print("Marker setup failed or marker environment could not be activated.")
            return 1

    snapshot = _load_index_with_lock(options.library_dir)
    results = run_fetch(papers, options, marker_cmd, snapshot)

    failures = 0
    updates: dict[str, dict] = {}
    for result in results:
        if result.success:
            if result.dry_run:
                print(f"[dry-run] {result.paper.title}")
            else:
                print(f"[ok] {result.paper.title}")
                if result.index_entry is not None:
                    updates[result.key] = result.index_entry
        else:
            failures += 1
            print(f"[fail] {result.paper.title}: {result.error}")

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
        print("No cache entries.")
        return 0

    for key, entry in rows:
        title = str(entry.get("title", ""))
        kind = str(entry.get("identity_kind", ""))
        value = str(entry.get("identity_value", ""))
        updated = str(entry.get("updated_at", ""))
        print(f"{key[:10]}  {kind}:{value}  {title}  {updated}")
    return 0


def _resolve_key_from_args(index: dict[str, dict], key: str | None, url: str | None) -> str | None:
    if key:
        return key
    if url:
        identity = build_identity(url)
        return identity.key
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
    meta_rel = str(entry.get("md", "")).replace("md/", "meta/").replace(".md", ".json")
    meta_path = library_dir / meta_rel
    if meta_path.exists():
        print(meta_path.read_text(encoding="utf-8"))
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

    marker_cmd: str | None = None
    if options.extractor != "arxiv_html":
        marker_cmd = ensure_marker_command(
            marker_venv=args.marker_venv.expanduser().resolve(),
            allow_install=not args.no_install_marker,
            verbose=args.verbose,
        )
        if marker_cmd is None and options.extractor == "marker":
            print("Marker setup failed or marker environment could not be activated.")
            return 1

    results = reextract_keys(sorted(keys), options, marker_cmd, idx_snapshot)
    failures = 0
    updates: dict[str, dict] = {}
    for result in results:
        if result.success:
            print(f"[ok] {result.paper.title}")
            if result.index_entry is not None:
                updates[result.key] = result.index_entry
        else:
            failures += 1
            print(f"[fail] {result.paper.title}: {result.error}")

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

    entries: list[dict] = []
    for key in keys:
        entry = idx.get(key)
        if entry:
            entries.append(entry)

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
            print("Semantic Scholar rate limit exceeded. Wait a moment and try again, or apply for a free API key at https://www.semanticscholar.org/product/api")
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


def _run_serve(args: argparse.Namespace) -> int:
    if not _HAS_FASTAPI:
        print("FastAPI/uvicorn not installed. Install with: pip install paperfetch-tool[serve]")
        return 1

    import uvicorn
    app = create_app(
        library_dir=args.library_dir,
        marker_venv=args.marker_venv,
    )
    print(f"Starting paperfetch API server at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _run_mcp(args: argparse.Namespace) -> int:
    if not _HAS_MCP:
        print("MCP server module not available.")
        return 1

    run_mcp_server(
        library_dir=args.library_dir,
        marker_venv=args.marker_venv,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        argv = ["fetch", *argv]
    elif argv[0] not in COMMANDS and argv[0] not in {"-h", "--help"}:
        argv = ["fetch", *argv]

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "fetch":
        return _run_fetch(args)
    if args.command == "list":
        return _run_list(args)
    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "reextract":
        return _run_reextract(args)
    if args.command == "clean":
        return _run_clean(args)
    if args.command == "export":
        return _run_export(args)
    if args.command == "discover":
        return _run_discover(args)
    if args.command == "serve":
        return _run_serve(args)
    if args.command == "mcp":
        return _run_mcp(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
