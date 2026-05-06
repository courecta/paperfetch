from __future__ import annotations

import json
from pathlib import Path

import pytest
from paperfetch_app.models import FetchOptions, PaperInput
from paperfetch_app.pipeline import process_one, run_fetch
from paperfetch_app.storage import load_index


class TestProcessOne:
    def test_dry_run(self, tmp_path: Path):
        paper = PaperInput(slug="test", title="Test", url="https://arxiv.org/abs/2401.00001", source="test")
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        result = process_one(paper, options, None, {})
        assert result.success
        assert result.dry_run
        from paperfetch_app.io_utils import sha256_text
        assert result.key == sha256_text("arxiv:2401.00001")

    def test_invalid_url(self, tmp_path: Path):
        paper = PaperInput(slug="bad", title="Bad URL", url="not-a-url", source="test")
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=False,
            download_timeout=5,
            marker_timeout=10,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        result = process_one(paper, options, None, {})
        assert not result.success
        assert result.error is not None

    def test_nonexistent_url(self, tmp_path: Path):
        paper = PaperInput(slug="missing", title="Missing", url="https://example.com/nonexistent.pdf", source="test")
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=False,
            download_timeout=5,
            marker_timeout=10,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        result = process_one(paper, options, None, {})
        assert not result.success
        assert "404" in result.error or "failed" in result.error


class TestRunFetch:
    def test_multiple_dry_runs(self, tmp_path: Path):
        papers = [
            PaperInput(slug=f"p{i}", title=f"Paper {i}", url=f"https://arxiv.org/abs/2401.{i:05d}", source="test")
            for i in range(10)
        ]
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=4,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        results = run_fetch(papers, options, None, {})
        assert len(results) == 10
        assert all(r.success for r in results)
        assert all(r.dry_run for r in results)

    def test_mixed_success_failure(self, tmp_path: Path):
        papers = [
            PaperInput(slug="good", title="Good", url="https://arxiv.org/abs/2401.00001", source="test"),
            PaperInput(slug="bad", title="Bad", url="not-a-url", source="test"),
        ]
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        results = run_fetch(papers, options, None, {})
        assert len(results) == 2
        assert results[0].success
        assert results[0].dry_run
        assert results[1].success  # dry_run doesn't validate URLs
        assert results[1].dry_run

    def test_identity_deduplication(self, tmp_path: Path):
        """Same URL should produce same key."""
        papers = [
            PaperInput(slug="a", title="A", url="https://arxiv.org/abs/2401.00001", source="test"),
            PaperInput(slug="b", title="B", url="https://arxiv.org/abs/2401.00001", source="test"),
        ]
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        results = run_fetch(papers, options, None, {})
        assert results[0].key == results[1].key


class TestEdgeCases:
    def test_very_long_slug(self, tmp_path: Path):
        long_title = "A" * 500
        paper = PaperInput(slug=long_title, title=long_title, url="https://arxiv.org/abs/2401.00001", source="test")
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        result = process_one(paper, options, None, {})
        assert result.success

    def test_unicode_in_title(self, tmp_path: Path):
        paper = PaperInput(slug="unicode", title="論文タイトル", url="https://arxiv.org/abs/2401.00001", source="test")
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        result = process_one(paper, options, None, {})
        assert result.success

    def test_empty_slug(self, tmp_path: Path):
        paper = PaperInput(slug="", title="", url="https://arxiv.org/abs/2401.00001", source="test")
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        result = process_one(paper, options, None, {})
        assert result.success

    def test_special_chars_in_url(self, tmp_path: Path):
        paper = PaperInput(slug="special", title="Special", url="https://example.com/paper?v=1&foo=bar", source="test")
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        result = process_one(paper, options, None, {})
        assert result.success

    def test_url_with_fragment(self, tmp_path: Path):
        paper = PaperInput(slug="frag", title="Fragment", url="https://arxiv.org/abs/2401.00001#section1", source="test")
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=1,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        result = process_one(paper, options, None, {})
        assert result.success


class TestStress:
    def test_large_manifest_dry_run(self, tmp_path: Path):
        """Stress test: process 1000 papers in dry-run mode."""
        papers = [
            PaperInput(slug=f"p{i}", title=f"Paper {i}", url=f"https://arxiv.org/abs/2401.{i:05d}", source="test")
            for i in range(1000)
        ]
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=8,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        import time
        start = time.time()
        results = run_fetch(papers, options, None, {})
        elapsed = time.time() - start

        assert len(results) == 1000
        assert all(r.success for r in results)
        assert elapsed < 30  # Should complete in under 30 seconds

    def test_concurrent_same_paper(self, tmp_path: Path):
        """Stress test: fetch same paper concurrently should not crash."""
        papers = [
            PaperInput(slug="same", title="Same", url="https://arxiv.org/abs/2401.00001", source="test")
            for _ in range(10)
        ]
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=10,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        results = run_fetch(papers, options, None, {})
        assert len(results) == 10
        assert all(r.success for r in results)
        # All should have same key
        keys = {r.key for r in results}
        assert len(keys) == 1

    def test_malformed_index_recovery(self, tmp_path: Path):
        """Test that malformed index is handled gracefully."""
        idx_file = tmp_path / "index.json"
        idx_file.write_text("not json")
        result = load_index(idx_file)
        assert result == {}

        idx_file.write_text("[1, 2, 3]")
        result = load_index(idx_file)
        assert result == {}

        idx_file.write_text('{"key1": "not_a_dict", "key2": {"valid": true}}')
        result = load_index(idx_file)
        assert result == {"key2": {"valid": True}}

    def test_corrupted_pdf_handling(self, tmp_path: Path):
        """Test handling of corrupted/non-PDF files."""
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "pdfs").mkdir()
        (lib / "md").mkdir()
        (lib / "meta").mkdir()

        # Write a text file as "pdf"
        pdf_file = lib / "pdfs" / "fake.pdf"
        pdf_file.write_text("This is not a PDF")

        # Write index pointing to it
        index = {
            "key1": {
                "slug": "fake",
                "title": "Fake",
                "pdf": "pdfs/fake.pdf",
                "md": "md/fake.md",
                "updated_at": "2024-01-01T00:00:00+00:00",
            }
        }
        (lib / "index.json").write_text(json.dumps(index))

        # clean should handle this gracefully
        from paperfetch_app.storage import clean_library
        result = clean_library(lib, index, prune_missing_entries=True, remove_orphans=False)
        assert result["removed_index_entries"] == 1  # MD is missing, so entry is pruned

    def test_missing_meta_directory(self, tmp_path: Path):
        """Test operations when meta directory doesn't exist."""
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "index.json").write_text("{}")

        result = load_index(lib / "index.json")
        assert result == {}

    def test_zero_workers(self, tmp_path: Path):
        """Test with workers=0 falls back to sequential."""
        papers = [
            PaperInput(slug="a", title="A", url="https://arxiv.org/abs/2401.00001", source="test"),
        ]
        options = FetchOptions(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
            download_timeout=30,
            marker_timeout=60,
            download_retries=0,
            marker_retries=0,
            download_backoff=1.0,
            marker_backoff=1.0,
            workers=0,
            min_md_chars=1,
            min_md_lines=1,
            allow_low_quality_md=False,
            verbose=False,
            extractor="auto",
        )
        results = run_fetch(papers, options, None, {})
        assert len(results) == 1
        assert results[0].success
