"""Pipeline behaviour that does not need network or a layout model.

Everything here runs in dry-run or fails before extraction, so it covers
routing, identity and error handling only. Extraction *quality* -- whether
tables have rows, figures have captions, coverage means anything -- cannot be
asserted from mocks and lives in scripts/validate.py against real papers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paperfetch_app.io_utils import sha256_text
from paperfetch_app.models import FetchOptions, PaperInput
from paperfetch_app.pipeline import process_one, run_fetch
from paperfetch_app.storage import load_index


@pytest.fixture
def options(tmp_path: Path):
    """One place to change when FetchOptions grows a field."""

    def build(**overrides) -> FetchOptions:
        base = dict(
            library_dir=tmp_path,
            out_dir=None,
            project_files="none",
            link_mode="copy",
            overwrite=False,
            force_download=False,
            refresh_md=False,
            dry_run=True,
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
        base.update(overrides)
        return FetchOptions(**base)

    return build


def paper(url: str, slug: str = "p", title: str = "T") -> PaperInput:
    return PaperInput(slug=slug, title=title, url=url, source="test")


class TestIdentity:
    def test_dry_run_keys_by_identity_not_url(self, options):
        result = process_one(paper("https://arxiv.org/abs/2401.00001"), options(), None, {})
        assert result.success and result.dry_run
        assert result.key == sha256_text("arxiv:2401.00001")

    @pytest.mark.parametrize(
        "urls",
        [
            ("https://arxiv.org/abs/2401.00001", "https://arxiv.org/abs/2401.00001v2"),
            ("https://arxiv.org/abs/2401.00001", "https://arxiv.org/pdf/2401.00001"),
            ("https://arxiv.org/abs/2401.00001", "https://arxiv.org/abs/2401.00001#section"),
        ],
    )
    def test_equivalent_urls_share_a_key(self, options, urls):
        keys = {process_one(paper(u), options(), None, {}).key for u in urls}
        assert len(keys) == 1, f"{urls} produced {keys}"


class TestFailureHandling:
    @pytest.mark.parametrize("url", ["not-a-url", "https://example.invalid/nothing.pdf", ""])
    def test_bad_input_fails_without_raising(self, options, url):
        result = process_one(paper(url), options(dry_run=False), None, {})
        assert not result.success
        assert result.error

    def test_every_paper_yields_a_result_even_when_some_fail(self, options):
        # All invalid, so the batch can be judged without network: the point is
        # that run_fetch reports each one instead of raising on the first.
        papers = [paper(u, slug=f"s{i}") for i, u in enumerate(["not-a-url", "", "http://"])]
        results = run_fetch(papers, options(dry_run=False), None, {})
        assert len(results) == len(papers)
        assert all(not r.success and r.error for r in results)

    def test_unreadable_index_does_not_crash_the_run(self, options, tmp_path: Path):
        (tmp_path / "index.json").write_text("{not json")
        assert load_index(tmp_path / "index.json") == {}
        assert process_one(paper("https://arxiv.org/abs/2401.00001"), options(), None, {}).success


class TestSlugHandling:
    @pytest.mark.parametrize(
        "slug,title",
        [
            ("x" * 300, "Long slug"),
            ("", "Empty slug"),
            ("p", "Ünïcödé 论文 — title"),
        ],
    )
    def test_awkward_slugs_and_titles_survive(self, options, slug, title):
        result = process_one(paper("https://arxiv.org/abs/2401.00001", slug=slug, title=title), options(), None, {})
        assert result.success


class TestConcurrency:
    def test_duplicate_papers_resolve_to_one_key(self, options):
        same = [paper("https://arxiv.org/abs/2401.00001", slug=f"s{i}") for i in range(5)]
        results = run_fetch(same, options(workers=4), None, {})
        assert len({r.key for r in results}) == 1

    def test_worker_count_is_clamped(self, options):
        results = run_fetch([paper("https://arxiv.org/abs/2401.00001")], options(workers=0), None, {})
        assert len(results) == 1 and results[0].success
