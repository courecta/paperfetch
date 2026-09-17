"""Background ingestion jobs."""

from __future__ import annotations

from pathlib import Path

from paperfetch_app.io_utils import write_json_atomic
from paperfetch_app.jobs import describe, get_job, job_path, list_jobs


def _record(library: Path, job_id: str, **over) -> dict:
    record = {
        "id": job_id,
        "state": "running",
        "library_dir": str(library),
        "urls": ["u1", "u2"],
        "total": 2,
        "done": 0,
        "succeeded": 0,
        "failed": 0,
        "results": [],
        "started_at": "2026-01-01T00:00:00Z",
        "current": None,
    }
    record.update(over)
    write_json_atomic(job_path(library, job_id), record)
    return record


class TestJobReporting:
    def test_running_job_names_the_paper_in_flight(self, tmp_path: Path):
        record = _record(tmp_path, "abc", current="https://arxiv.org/abs/1", done=1, succeeded=1)
        summary = describe(record)
        assert "abc" in summary and "running (1/2)" in summary
        assert "now: https://arxiv.org/abs/1" in summary

    def test_finished_job_does_not_claim_work_in_flight(self, tmp_path: Path):
        record = _record(tmp_path, "abc", state="completed", done=2, succeeded=2, current="leftover")
        assert "now:" not in describe(record)
        assert "2 ok" in describe(record)

    def test_failures_are_surfaced(self, tmp_path: Path):
        record = _record(tmp_path, "abc", state="completed", done=2, succeeded=1, failed=1)
        assert "1 failed" in describe(record)


class TestJobStorage:
    def test_job_round_trips(self, tmp_path: Path):
        _record(tmp_path, "abc")
        assert get_job(tmp_path, "abc")["id"] == "abc"

    def test_unknown_job_is_none(self, tmp_path: Path):
        assert get_job(tmp_path, "nope") is None

    def test_jobs_list_newest_first(self, tmp_path: Path):
        _record(tmp_path, "old", started_at="2026-01-01T00:00:00Z")
        _record(tmp_path, "new", started_at="2026-06-01T00:00:00Z")
        assert [j["id"] for j in list_jobs(tmp_path)] == ["new", "old"]

    def test_library_with_no_jobs_is_not_an_error(self, tmp_path: Path):
        assert list_jobs(tmp_path) == []
