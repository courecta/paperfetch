from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from . import assets as assets_module
from .bibtex import generate_bibtex
from .bundle import BundlePaths, bundle_paths, promote, staging_bundle, write_checksums
from .config import get_default_marker_venv
from .errors import (
    CoverageError,
    ExtractionError,
    MarkerUnavailableError,
    PaperfetchError,
)
from .extract.coverage import audit
from .extract.html_arxiv import extract_arxiv_document
from .extract.ir import Document
from .extract.marker_ir import document_from_marker_json
from .extract.render_markdown import render_markdown
from .extract.render_text import render_text, strip_markdown
from .fetch.http import HttpClient
from .identity import build_identity
from .io_utils import ensure_dir, now_utc_iso, safe_slug, write_json_atomic
from .locking import file_lock
from .marker_backend import ensure_marker_command, extract_with_marker
from .metadata import fetch_arxiv_metadata
from .models import FetchOptions, PaperInput, ProcessResult
from .pdf_visual import render_pdf_visuals
from .pymupdf_backend import extract_with_pymupdf, pymupdf_available
from .resolve.base import STATUS_UNAVAILABLE, Resolution, SourceCandidate, resolve
from .storage import materialize

INDEX_FIELDS = (
    "authors",
    "abstract",
    "year",
    "venue",
    "categories",
    "primary_category",
    "doi",
    "arxiv_id",
    "published",
    "updated",
    "bibtex",
    "license",
)


# --------------------------------------------------------------------- helpers
def _build_client(options: FetchOptions) -> HttpClient:
    return HttpClient(
        timeout=options.download_timeout,
        retries=options.download_retries,
        backoff=options.download_backoff,
    )


def _collect_metadata(
    identity: Any,
    resolution: Resolution,
    client: HttpClient,
    options: FetchOptions,
) -> dict[str, Any]:
    metadata: dict[str, Any] = dict(resolution.metadata or {})
    if resolution.title and not metadata.get("title"):
        metadata["title"] = resolution.title
    if resolution.authors and not metadata.get("authors"):
        metadata["authors"] = list(resolution.authors)
    if resolution.abstract and not metadata.get("abstract"):
        metadata["abstract"] = resolution.abstract
    if resolution.year and not metadata.get("year"):
        metadata["year"] = resolution.year
    if resolution.venue and not metadata.get("venue"):
        metadata["venue"] = resolution.venue
    if resolution.doi and not metadata.get("doi"):
        metadata["doi"] = resolution.doi

    if identity.kind == "arxiv":
        try:
            arxiv_meta = fetch_arxiv_metadata(
                identity.value,
                timeout_sec=options.download_timeout,
                retries=options.download_retries,
                backoff=options.download_backoff,
                client=client,
            )
            for key, value in arxiv_meta.to_dict().items():
                if value not in (None, "", []):
                    metadata[key] = value
        except Exception as exc:
            resolution.notes.append(f"arxiv metadata unavailable: {exc}")
    return metadata


def _download_pdf(
    resolution: Resolution,
    options: FetchOptions,
    client: HttpClient,
    destination: Path,
) -> SourceCandidate | None:
    if options.pdf_path is not None:
        source = options.pdf_path.expanduser().resolve()
        if not source.is_file():
            raise PaperfetchError(f"Local PDF not found: {source}")
        ensure_dir(destination.parent)
        shutil.copy2(source, destination)
        return SourceCandidate(kind="pdf", url=str(source), extractor="local", label="Local PDF")

    last_error: Exception | None = None
    for candidate in resolution.ordered_candidates():
        if candidate.kind != "pdf":
            continue
        try:
            client.download(
                candidate.url,
                destination,
                expect_pdf=True,
                max_bytes=options.max_asset_bytes * 4,
                timeout=options.download_timeout,
                retries=options.download_retries,
                backoff=options.download_backoff,
            )
            return candidate
        except PaperfetchError as exc:
            last_error = exc
            resolution.notes.append(f"PDF candidate failed ({candidate.url}): {exc}")
            continue
    if last_error is not None:
        raise last_error
    return None


def _extract_pdf_document(
    resolution: Resolution,
    options: FetchOptions,
    client: HttpClient,
    staging: BundlePaths,
    key: str,
    marker_cmd: str | None,
    metadata: dict[str, Any],
) -> tuple[Document, str, str, dict[str, Any]]:
    """Run marker (preferred) or PyMuPDF and return (document, markdown, source_kind, assets)."""
    if options.extractor == "pymupdf" or options.prefer_pymupdf:
        marker_cmd = None
    else:
        marker_cmd = marker_cmd or ensure_marker_command(
            (options.marker_venv or get_default_marker_venv()).expanduser().resolve(),
            options.install_marker,
            options.verbose,
        )
        if options.extractor == "marker" and marker_cmd is None:
            raise MarkerUnavailableError(
                "marker is not installed and could not be installed",
                hint="Install marker-pdf, allow auto-install, or pass --prefer-pymupdf.",
            )

    use_pymupdf = options.extractor == "pymupdf" or options.prefer_pymupdf or marker_cmd is None
    last_error: Exception | None = None

    if not use_pymupdf:
        try:
            work_dir = staging.root / "converted"
            result = extract_with_marker(
                marker_cmd=marker_cmd,
                pdf_path=staging.pdf,
                work_dir=work_dir,
                timeout_sec=options.marker_timeout,
                retries=options.marker_retries,
                backoff_seconds=options.marker_backoff,
                verbose=options.verbose,
                output_format="json",
            )
            if result.json_path is None:
                raise ExtractionError("marker produced no JSON output")
            payload = json.loads(result.json_path.read_text(encoding="utf-8", errors="replace"))
            document = document_from_marker_json(
                payload,
                key=key,
                source_url=resolution.identity.normalized_url or "",
                figures_dir=staging.figures_dir,
                title=str(metadata.get("title") or ""),
            )
            # markdown_text is None so the caller renders it from the IR, the
            # same path the arXiv HTML extractor takes.
            return document, None, "marker", document.coverage.get("assets", {})
        except Exception as exc:
            last_error = exc
            if options.extractor == "marker":
                raise ExtractionError(f"marker extraction failed: {exc}") from exc

    if not pymupdf_available():
        if last_error is not None:
            raise ExtractionError(
                f"PDF extraction failed with marker ({last_error}) and PyMuPDF is not installed"
            ) from last_error
        raise MarkerUnavailableError(
            "No PDF extractor available",
            hint="Install marker-pdf or pymupdf, or use --extractor marker.",
        )

    try:
        work_dir = staging.root / "converted-pymupdf"
        result = extract_with_pymupdf(staging.pdf, work_dir)
        markdown_text = result.markdown_path.read_text(encoding="utf-8", errors="replace")
        markdown_text, asset_report = assets_module.ingest_marker_output(
            markdown_text, result.output_dir, staging
        )
        document = _pdf_shell_document(key, resolution, metadata, "pymupdf")
        document.coverage["assets"] = asset_report.to_dict()
        return document, markdown_text, "pymupdf", asset_report.to_dict()
    except Exception as exc:
        raise ExtractionError(f"PyMuPDF extraction failed: {exc}") from exc


def _pdf_shell_document(
    key: str,
    resolution: Resolution,
    metadata: dict[str, Any],
    source_kind: str,
) -> Document:
    document = Document(
        schema_version=1,
        key=key,
        source_kind=source_kind,
        source_url=resolution.landing_url or resolution.identity.normalized_url,
        title=str(metadata.get("title") or ""),
    )
    document.coverage["ir_available"] = False
    return document


def _apply_visuals(document: Document, report: dict[str, Any]) -> None:
    pages = report.get("float_pages", {}) if isinstance(report, dict) else {}
    crops = {entry["id"]: entry for entry in report.get("crops", [])} if isinstance(report, dict) else {}
    for block in document.blocks:
        if block.kind not in {"figure", "table"}:
            continue
        if block.id in pages:
            block.meta["pdf_page"] = pages[block.id]
        if block.id in crops:
            block.meta["pdf_crop"] = crops[block.id].get("path")


def _write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _entry_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "slug": meta.get("slug"),
        "title": meta.get("title"),
        "source_url": meta.get("source_url"),
        "identity_kind": meta.get("identity_kind"),
        "identity_value": meta.get("identity_value"),
        "identity_fingerprint": meta.get("identity_fingerprint"),
        "pdf": meta.get("pdf"),
        "md": meta.get("md"),
        "document": meta.get("document"),
        "extractor": meta.get("extractor"),
        "updated_at": meta.get("updated_at") or now_utc_iso(),
        "coverage_ratio": (meta.get("coverage") or {}).get("ratio"),
        "coverage_ok": (meta.get("coverage") or {}).get("ok"),
    }
    for field in INDEX_FIELDS:
        if meta.get(field):
            entry[field] = meta[field]
    return entry


def _relative(path: Path, root: Path) -> str | None:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return None


def _write_project_files(final: BundlePaths, slug: str, options: FetchOptions) -> None:
    if not options.out_dir:
        return
    out_slug = safe_slug(slug)
    wants_md = options.project_files in {"md", "both"}
    wants_figures = options.project_files in {"md", "both", "figures"}
    figures_target = options.out_dir / f"{out_slug}-figures"

    if wants_figures and final.figures_dir.exists():
        if figures_target.exists() and options.overwrite:
            shutil.rmtree(figures_target, ignore_errors=True)
        if not figures_target.exists():
            shutil.copytree(final.figures_dir, figures_target)

    if wants_md and final.markdown.exists():
        text = final.markdown.read_text(encoding="utf-8", errors="replace")
        if wants_figures and figures_target.exists():
            text = text.replace("](figures/", f"]({out_slug}-figures/")
        _write_text(options.out_dir / f"{out_slug}.md", text)

    if options.project_files == "both" and final.pdf.exists():
        materialize(final.pdf, options.out_dir / f"{out_slug}.pdf", mode=options.link_mode, overwrite=options.overwrite)


# ----------------------------------------------------------------------- main
def process_one(
    paper: PaperInput,
    options: FetchOptions,
    marker_cmd: str | None = None,
    index_snapshot: dict[str, dict[str, Any]] | None = None,
    client: HttpClient | None = None,
) -> ProcessResult:
    index_snapshot = index_snapshot or {}
    identity = build_identity(paper.url)
    key = identity.key
    existing = index_snapshot.get(key, {})
    chosen_slug = (
        existing.get("slug")
        if isinstance(existing.get("slug"), str) and existing.get("slug")
        else safe_slug(paper.slug or paper.title or Path(identity.value).stem or "paper")
    )
    final = bundle_paths(options.library_dir, key)

    if options.dry_run:
        return ProcessResult(success=True, paper=paper, key=key, dry_run=True)

    if final.meta.exists() and not options.refresh_md and not options.force_download:
        from .io_utils import read_json

        meta = read_json(final.meta, default={}) or {}
        return ProcessResult(
            success=True,
            paper=paper,
            key=key,
            index_entry=_entry_from_meta(meta),
            coverage=meta.get("coverage"),
        )

    owns_client = client is None
    client = client or _build_client(options)
    lock_path = options.library_dir / "locks" / f"{key}.lock"
    try:
        with file_lock(lock_path, timeout_sec=180.0):
            with staging_bundle(options.library_dir, key) as staging:
                if final.meta.exists() and not options.refresh_md and not options.force_download:
                    from .io_utils import read_json

                    meta = read_json(final.meta, default={}) or {}
                    return ProcessResult(
                        success=True,
                        paper=paper,
                        key=key,
                        index_entry=_entry_from_meta(meta),
                        coverage=meta.get("coverage"),
                    )

                resolution = resolve(identity, client)
                if resolution.status == STATUS_UNAVAILABLE and not resolution.candidates:
                    raise PaperfetchError(
                        f"No open-access source found for {paper.url}",
                        hint="Provide a direct PDF URL or a local PDF with --pdf.",
                    )
                metadata = _collect_metadata(identity, resolution, client, options)

                document: Document | None = None
                source_html: str | None = None
                markdown_text: str | None = None
                used_extractor: str | None = None
                asset_report: dict[str, Any] = {}

                html_candidate = next(
                    (c for c in resolution.ordered_candidates() if c.extractor == "arxiv_html"), None
                )
                if html_candidate is not None and options.extractor in {"auto", "arxiv_html"}:
                    try:
                        html_text = client.get_text(
                            html_candidate.url,
                            timeout=options.download_timeout,
                            retries=options.download_retries,
                            backoff=options.download_backoff,
                        )
                        document = extract_arxiv_document(
                            paper_id=resolution.arxiv_id or identity.value,
                            key=key,
                            source_url=html_candidate.url,
                            html_text=html_text,
                            client=client,
                        )
                        source_html = html_text
                        used_extractor = "arxiv_html"
                    except Exception as exc:
                        if options.extractor == "arxiv_html":
                            raise ExtractionError(f"arXiv HTML extraction failed: {exc}") from exc
                        resolution.notes.append(f"arXiv HTML unavailable: {exc}")

                pdf_error: Exception | None = None
                if document is None or options.pdf_visual:
                    try:
                        _download_pdf(resolution, options, client, staging.pdf)
                    except PaperfetchError as exc:
                        pdf_error = exc
                        if document is None:
                            raise

                if document is None:
                    if not staging.pdf.exists():
                        raise pdf_error or PaperfetchError("No PDF could be downloaded for this paper")
                    document, markdown_text, used_extractor, asset_report = _extract_pdf_document(
                        resolution, options, client, staging, key, marker_cmd, metadata
                    )

                if used_extractor == "arxiv_html":
                    if options.download_figures:
                        figure_report = assets_module.ingest_figure_assets(
                            document, staging, client, max_bytes=options.max_asset_bytes
                        )
                        asset_report = figure_report.to_dict()
                    coverage_report = audit(document, min_ratio=options.min_coverage)

                    # Only fall back when the HTML extraction produced nothing
                    # usable. A partially-covered IR still carries sections,
                    # captions and tables, so trading it for a PDF re-extraction
                    # loses more than it recovers.
                    if not coverage_report["ok"] and options.extractor == "auto" and coverage_report["empty"]:
                        resolution.notes.append(
                            f"arXiv HTML unusable ({coverage_report.get('reason')}); falling back to PDF"
                        )
                        try:
                            if not staging.pdf.exists():
                                _download_pdf(resolution, options, client, staging.pdf)
                            document, markdown_text, used_extractor, asset_report = _extract_pdf_document(
                                resolution, options, client, staging, key, marker_cmd, metadata
                            )
                            coverage_report = audit(document, min_ratio=options.min_coverage)
                        except Exception as exc:
                            resolution.notes.append(f"PDF fallback failed: {exc}")

                else:
                    coverage_report = audit(document, min_ratio=options.min_coverage)

                # The gate applies to every path, not just arXiv HTML: a PDF
                # extraction that yields no structured IR cannot make a
                # fidelity claim either.
                if not coverage_report["ok"] and not options.allow_incomplete:
                    raise CoverageError(
                        coverage_report.get("reason") or f"coverage below {options.min_coverage}",
                        report=coverage_report,
                    )

                visual_report: dict[str, Any] = {"available": False, "reason": "disabled"}
                if options.pdf_visual and staging.pdf.exists():
                    visual_report = render_pdf_visuals(document, staging.pdf, staging)
                    _apply_visuals(document, visual_report)
                    assets_module.write_figure_manifest(document, staging)

                if markdown_text is None:
                    markdown_text = render_markdown(document, metadata=metadata)
                if used_extractor == "pymupdf":
                    text_output = strip_markdown(markdown_text)
                else:
                    text_output = render_text(document, metadata=metadata)

                _write_text(staging.markdown, markdown_text)
                _write_text(staging.text, text_output)
                # Any extractor that produces table blocks gets sidecars, not
                # just arXiv HTML; marker's layout model finds tables too.
                if any(block.kind == "table" for block in document.blocks):
                    assets_module.write_table_sidecars(document, staging)
                if source_html is not None:
                    _write_text(staging.source, source_html)
                write_json_atomic(staging.document, document.to_dict())

                refs_bib = str(metadata.get("bibtex") or "").strip() or generate_bibtex(metadata)
                _write_text(staging.refs_bib, refs_bib + "\n")
                write_json_atomic(
                    staging.references,
                    [entry.to_dict() for entry in document.bibliography],
                )

                meta_payload: dict[str, Any] = {
                    "key": key,
                    "slug": chosen_slug,
                    "title": metadata.get("title") or paper.title,
                    "source_url": resolution.landing_url or identity.normalized_url,
                    "identity_kind": identity.kind,
                    "identity_value": identity.value,
                    "identity_fingerprint": identity.fingerprint,
                    "extractor": used_extractor,
                    "source_kind": document.source_kind,
                    "pdf": _relative(staging.pdf, staging.root),
                    "md": _relative(staging.markdown, staging.root),
                    "document": _relative(staging.document, staging.root),
                    "figures": _relative(staging.figures_manifest(), staging.root),
                    "updated_at": now_utc_iso(),
                    "input_source": paper.source,
                    "coverage": coverage_report,
                    "resolution": resolution.to_dict(),
                    "notes": resolution.notes,
                }
                for field in INDEX_FIELDS:
                    if metadata.get(field) not in (None, "", []):
                        meta_payload[field] = metadata[field]
                write_json_atomic(staging.meta, meta_payload)

                report = {
                    "key": key,
                    "extractor": used_extractor,
                    "coverage": coverage_report,
                    "assets": asset_report,
                    "pdf_visuals": visual_report,
                    "resolution": resolution.to_dict(),
                    "notes": resolution.notes,
                }
                write_json_atomic(staging.report, report)
                write_checksums(staging)
                promote(staging.root, final.root)

            _write_project_files(final, chosen_slug, options)

        from .io_utils import read_json

        meta = read_json(final.meta, default={}) or {}
        return ProcessResult(
            success=True,
            paper=paper,
            key=key,
            index_entry=_entry_from_meta(meta),
            resolution=meta.get("resolution"),
            coverage=meta.get("coverage"),
        )
    except Exception as exc:
        return ProcessResult(success=False, paper=paper, key=key, error=str(exc))
    finally:
        if owns_client:
            client.close()


def run_fetch(
    papers: list[PaperInput],
    options: FetchOptions,
    marker_cmd: str | None = None,
    index_snapshot: dict[str, dict[str, Any]] | None = None,
) -> list[ProcessResult]:
    index_snapshot = index_snapshot or {}
    if options.dry_run:
        return [process_one(p, options, marker_cmd, index_snapshot) for p in papers]

    client = _build_client(options)
    try:
        if options.workers <= 1 or len(papers) <= 1:
            return [process_one(p, options, marker_cmd, index_snapshot, client=client) for p in papers]

        results: list[ProcessResult] = []
        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            futures = [
                pool.submit(process_one, paper, options, marker_cmd, index_snapshot, client) for paper in papers
            ]
            for future in as_completed(futures):
                results.append(future.result())
        return results
    finally:
        client.close()


def reextract_keys(
    keys: list[str],
    options: FetchOptions,
    marker_cmd: str | None,
    index_snapshot: dict[str, dict[str, Any]],
) -> list[ProcessResult]:
    tasks: list[PaperInput] = []
    for key in keys:
        entry = index_snapshot.get(key)
        if not entry:
            continue
        tasks.append(
            PaperInput(
                slug=str(entry.get("slug", "paper")),
                title=str(entry.get("title", "paper")),
                url=str(entry.get("source_url", "")),
                source="reextract",
            )
        )
    if not tasks:
        return []
    reextract_options = FetchOptions(
        library_dir=options.library_dir,
        out_dir=options.out_dir,
        project_files=options.project_files,
        link_mode=options.link_mode,
        overwrite=options.overwrite,
        force_download=False,
        refresh_md=True,
        dry_run=options.dry_run,
        download_timeout=options.download_timeout,
        marker_timeout=options.marker_timeout,
        download_retries=options.download_retries,
        marker_retries=options.marker_retries,
        download_backoff=options.download_backoff,
        marker_backoff=options.marker_backoff,
        workers=options.workers,
        min_md_chars=options.min_md_chars,
        min_md_lines=options.min_md_lines,
        allow_low_quality_md=options.allow_low_quality_md,
        verbose=options.verbose,
        extractor=options.extractor,
        min_coverage=options.min_coverage,
        allow_incomplete=options.allow_incomplete,
        download_figures=options.download_figures,
        pdf_visual=options.pdf_visual,
        max_asset_bytes=options.max_asset_bytes,
        marker_venv=options.marker_venv,
        install_marker=options.install_marker,
        prefer_pymupdf=options.prefer_pymupdf,
        pdf_path=options.pdf_path,
    )
    return run_fetch(tasks, reextract_options, marker_cmd, index_snapshot)
