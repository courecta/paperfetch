from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
from pathlib import Path
from typing import Any

import requests

from . import arxiv_html, metadata
from .identity import build_identity
from .io_utils import check_markdown_quality, ensure_dir, now_utc_iso, retry_call, safe_slug, sha256_file, write_json_atomic
from .locking import file_lock
from .marker_backend import extract_with_marker
from .models import FetchOptions, PaperInput, ProcessResult
from .storage import ensure_library_paths, materialize


USER_AGENT = "paperfetch/2.0 (requests)"


def _download_pdf(url: str, out_path: Path, timeout_sec: int, retries: int, backoff: float) -> None:
    headers = {"User-Agent": USER_AGENT}

    def _once() -> None:
        with requests.get(url, headers=headers, stream=True, timeout=timeout_sec) as response:
            response.raise_for_status()
            ensure_dir(out_path.parent)
            tmp = out_path.with_suffix(out_path.suffix + ".tmp")
            with tmp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
            tmp.replace(out_path)

    retry_call(
        action_name="download",
        func=_once,
        retries=retries,
        backoff_seconds=backoff,
        retriable_exceptions=(requests.RequestException, OSError),
    )


def _resolve_extractor(identity_kind: str, requested: str) -> str:
    if requested != "auto":
        return requested
    if identity_kind == "arxiv":
        return "arxiv_html"
    return "marker"


def _build_meta(
    key: str,
    paper: PaperInput,
    identity_kind: str,
    identity_value: str,
    identity_fingerprint: str,
    pdf_path: Path,
    md_path: Path,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "key": key,
        "slug": safe_slug(paper.slug),
        "title": paper.title,
        "source_url": paper.url,
        "identity_kind": identity_kind,
        "identity_value": identity_value,
        "identity_fingerprint": identity_fingerprint,
        "pdf": str(pdf_path),
        "md": str(md_path),
        "pdf_sha256": sha256_file(pdf_path),
        "updated_at": now_utc_iso(),
        "input_source": paper.source,
    }
    if extra:
        out.update(extra)
    return out


def _write_md(md_path: Path, content: str) -> None:
    ensure_dir(md_path.parent)
    tmp = md_path.with_suffix(md_path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(md_path)


def _extract_with_arxiv_html(
    paper_id: str,
    store: Any,
    options: FetchOptions,
) -> tuple[str, dict[str, Any]]:
    """Extract markdown using arXiv HTML. Returns (markdown, metadata_dict)."""
    meta = metadata.fetch_arxiv_metadata(
        paper_id,
        timeout_sec=options.download_timeout,
        retries=options.download_retries,
        backoff=options.download_backoff,
    )
    md_content = arxiv_html.extract_arxiv_markdown(
        paper_id,
        timeout_sec=options.download_timeout,
        retries=options.download_retries,
        backoff=options.download_backoff,
    )

    if not options.allow_low_quality_md:
        # Write temporarily to check quality
        _write_md(store.md, md_content)
        ok, reason = check_markdown_quality(
            store.md,
            min_chars=options.min_md_chars,
            min_lines=options.min_md_lines,
        )
        if not ok:
            raise RuntimeError(f"arxiv html quality check failed: {reason}")

    extra_meta = {
        "authors": meta.authors,
        "abstract": meta.abstract,
        "year": meta.year,
        "categories": meta.categories,
        "primary_category": meta.primary_category,
        "doi": meta.doi,
        "published": meta.published,
        "updated": meta.updated,
        "bibtex": meta.bibtex,
        "extractor": "arxiv_html",
    }
    return md_content, extra_meta


def _extract_with_marker(
    marker_cmd: str,
    pdf_path: Path,
    slug: str,
    options: FetchOptions,
) -> Path:
    extracted = extract_with_marker(
        marker_cmd=marker_cmd,
        pdf_path=pdf_path,
        slug=slug,
        marker_out_dir=options.library_dir / "converted",
        timeout_sec=options.marker_timeout,
        retries=options.marker_retries,
        backoff_seconds=options.marker_backoff,
        verbose=options.verbose,
    )
    return extracted


def process_one(
    paper: PaperInput,
    options: FetchOptions,
    marker_cmd: str | None,
    index_snapshot: dict[str, dict[str, Any]],
) -> ProcessResult:
    identity = build_identity(paper.url)
    key = identity.key
    existing = index_snapshot.get(key, {})
    chosen_slug = existing.get("slug") if isinstance(existing.get("slug"), str) else safe_slug(paper.slug)
    canonical_base = f"{chosen_slug}-{key[:8]}"
    store = ensure_library_paths(options.library_dir, canonical_base)

    if options.dry_run:
        return ProcessResult(success=True, paper=paper, key=key, dry_run=True)

    extractor = _resolve_extractor(identity.kind, options.extractor)

    lock_path = options.library_dir / "locks" / f"{key}.lock"

    try:
        with file_lock(lock_path, timeout_sec=180.0):
            # Always download PDF for cache (even if we use HTML extraction)
            if options.force_download or not store.pdf.exists():
                _download_pdf(
                    url=identity.normalized_url,
                    out_path=store.pdf,
                    timeout_sec=options.download_timeout,
                    retries=options.download_retries,
                    backoff=options.download_backoff,
                )

            extra_meta: dict[str, Any] = {}
            used_extractor = extractor

            if options.refresh_md or not store.md.exists():
                if extractor == "arxiv_html" and identity.kind == "arxiv":
                    try:
                        md_content, extra_meta = _extract_with_arxiv_html(
                            paper_id=identity.value,
                            store=store,
                            options=options,
                        )
                        _write_md(store.md, md_content)
                    except Exception as html_exc:
                        if options.extractor == "auto":
                            if options.verbose:
                                print(f"[arxiv_html] failed for {paper.title}, falling back to marker: {html_exc}")
                            used_extractor = "marker"
                        else:
                            raise

                if used_extractor == "marker":
                    if marker_cmd is None:
                        return ProcessResult(success=False, paper=paper, key=key, error="marker command unavailable")

                    extracted_path = _extract_with_marker(
                        marker_cmd=marker_cmd,
                        pdf_path=store.pdf,
                        slug=chosen_slug,
                        options=options,
                    )
                    ensure_dir(store.md.parent)
                    shutil.copy2(extracted_path, store.md)

                    extra_meta = {"extractor": "marker"}

                if not options.allow_low_quality_md:
                    ok, reason = check_markdown_quality(
                        store.md,
                        min_chars=options.min_md_chars,
                        min_lines=options.min_md_lines,
                    )
                    if not ok:
                        raise RuntimeError(reason)

            meta = _build_meta(
                key=key,
                paper=paper,
                identity_kind=identity.kind,
                identity_value=identity.value,
                identity_fingerprint=identity.fingerprint,
                pdf_path=store.pdf,
                md_path=store.md,
                extra=extra_meta,
            )
            write_json_atomic(store.meta, meta)

            index_entry: dict[str, Any] = {
                "slug": chosen_slug,
                "title": paper.title,
                "source_url": identity.normalized_url,
                "identity_kind": identity.kind,
                "identity_value": identity.value,
                "identity_fingerprint": identity.fingerprint,
                "pdf": str(store.pdf.relative_to(options.library_dir)),
                "md": str(store.md.relative_to(options.library_dir)),
                "updated_at": now_utc_iso(),
                "extractor": extra_meta.get("extractor", "marker"),
            }
            # Add metadata fields if available
            for field in ("authors", "abstract", "year", "categories", "primary_category", "doi", "published", "updated", "bibtex"):
                if field in extra_meta:
                    index_entry[field] = extra_meta[field]

            if options.out_dir:
                out_pdf = options.out_dir / f"{safe_slug(paper.slug)}.pdf"
                out_md = options.out_dir / f"{safe_slug(paper.slug)}.md"
                if options.project_files == "both":
                    materialize(store.pdf, out_pdf, mode=options.link_mode, overwrite=options.overwrite)
                if options.project_files in {"md", "both"}:
                    materialize(store.md, out_md, mode=options.link_mode, overwrite=options.overwrite)

        return ProcessResult(success=True, paper=paper, key=key, index_entry=index_entry)
    except Exception as exc:
        return ProcessResult(success=False, paper=paper, key=key, error=str(exc))


def run_fetch(
    papers: list[PaperInput],
    options: FetchOptions,
    marker_cmd: str | None,
    index_snapshot: dict[str, dict[str, Any]],
) -> list[ProcessResult]:
    if options.workers <= 1 or len(papers) <= 1:
        return [process_one(p, options, marker_cmd, index_snapshot) for p in papers]

    results: list[ProcessResult] = []
    with ThreadPoolExecutor(max_workers=options.workers) as pool:
        fut_map = {
            pool.submit(process_one, paper, options, marker_cmd, index_snapshot): paper
            for paper in papers
        }
        for fut in as_completed(fut_map):
            results.append(fut.result())
    return results


def reextract_keys(
    keys: list[str],
    options: FetchOptions,
    marker_cmd: str,
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
    )

    return run_fetch(tasks, reextract_options, marker_cmd, index_snapshot)
