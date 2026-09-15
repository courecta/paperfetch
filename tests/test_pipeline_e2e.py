from __future__ import annotations

from pathlib import Path

import pytest

from paperfetch_app.bundle import bundle_paths
from paperfetch_app.models import FetchOptions, PaperInput
from paperfetch_app.pipeline import process_one
from paperfetch_app.pymupdf_backend import pymupdf_available

pytestmark = pytest.mark.skipif(not pymupdf_available(), reason="PyMuPDF not installed")


def _make_pdf(path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A Local Test Paper")
    page.insert_text((72, 120), "Abstract: we test the paperfetch pipeline end to end.")
    page.insert_text((72, 160), "1 Introduction")
    page.insert_text((72, 200), "Anomaly detection is important.")
    doc.save(str(path))
    doc.close()


def _options(library: Path, pdf_path: Path, out_dir: Path | None = None) -> FetchOptions:
    return FetchOptions(
        library_dir=library,
        out_dir=out_dir,
        project_files="md",
        link_mode="copy",
        force_download=False,
        refresh_md=False,
        dry_run=False,
        download_timeout=5,
        marker_timeout=30,
        download_retries=0,
        marker_retries=0,
        download_backoff=0.0,
        marker_backoff=0.0,
        workers=1,
        extractor="pymupdf",
        install_marker=False,
        prefer_pymupdf=True,
        pdf_visual=False,
        pdf_path=pdf_path,
    )


def test_local_pdf_pipeline_bundle(tmp_path: Path):
    pdf_path = tmp_path / "local.pdf"
    _make_pdf(pdf_path)
    library = tmp_path / "lib"
    out_dir = tmp_path / "project"
    paper = PaperInput(slug="local-paper", title="Local Test Paper", url="https://example.com/local.pdf", source="test")

    result = process_one(paper, _options(library, pdf_path, out_dir), None, {})

    assert result.success, result.error
    final = bundle_paths(library, result.key)
    assert final.meta.is_file()
    assert final.markdown.is_file()
    assert final.text.is_file()
    assert final.document.is_file()
    assert final.pdf.is_file()
    assert final.checksums.is_file()
    assert final.report.is_file()
    assert "Anomaly detection" in final.markdown.read_text(encoding="utf-8")

    materialized = out_dir / "local-paper.md"
    assert materialized.is_file()
    assert "Anomaly detection" in materialized.read_text(encoding="utf-8")


def test_dry_run_does_not_touch_disk(tmp_path: Path):
    pdf_path = tmp_path / "local.pdf"
    _make_pdf(pdf_path)
    library = tmp_path / "lib"
    options = _options(library, pdf_path)
    options = FetchOptions(**{**options.__dict__, "dry_run": True})
    paper = PaperInput(slug="x", title="X", url="https://example.com/local.pdf", source="test")
    result = process_one(paper, options, None, {})
    assert result.success and result.dry_run
    assert not library.exists() or not any(library.iterdir())


def test_existing_bundle_is_not_refetched(tmp_path: Path):
    pdf_path = tmp_path / "local.pdf"
    _make_pdf(pdf_path)
    library = tmp_path / "lib"
    paper = PaperInput(slug="local-paper", title="Local Test Paper", url="https://example.com/local.pdf", source="test")
    first = process_one(paper, _options(library, pdf_path), None, {})
    assert first.success

    pdf_path.unlink()
    second = process_one(paper, _options(library, pdf_path), None, {})
    assert second.success
    assert second.index_entry is not None
