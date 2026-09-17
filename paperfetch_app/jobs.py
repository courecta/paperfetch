"""Background ingestion jobs.

Extraction takes minutes per paper, which is far longer than an agent client
will hold a tool call open. A fetch of any size therefore has to start, return
a handle, and be polled -- so the agent can say "started, 3 of 12 done" rather
than blocking until a timeout kills the call.

Jobs are files under <library>/jobs/. The runner is a detached subprocess that
rewrites its job file after every paper, so progress survives the MCP server
being restarted and can be read by anything, including the CLI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from .io_utils import ensure_dir, now_utc_iso, read_json, write_json_atomic

TERMINAL_STATES = {"completed", "failed"}


def jobs_dir(library_dir: Path) -> Path:
    return library_dir / "jobs"


def job_path(library_dir: Path, job_id: str) -> Path:
    return jobs_dir(library_dir) / f"{job_id}.json"


def start_fetch_job(
    library_dir: Path,
    urls: list[str],
    *,
    extractor: str = "auto",
    out_dir: Path | None = None,
    project_files: str = "none",
    marker_venv: Path | None = None,
) -> dict[str, Any]:
    """Write a job file and spawn a detached runner. Returns the job record."""
    library_dir = library_dir.expanduser().resolve()
    ensure_dir(jobs_dir(library_dir))
    job_id = uuid.uuid4().hex[:12]

    record: dict[str, Any] = {
        "id": job_id,
        "state": "pending",
        "library_dir": str(library_dir),
        "urls": list(urls),
        "extractor": extractor,
        "out_dir": str(out_dir) if out_dir else None,
        "project_files": project_files,
        "marker_venv": str(marker_venv) if marker_venv else None,
        "total": len(urls),
        "done": 0,
        "succeeded": 0,
        "failed": 0,
        "results": [],
        "started_at": now_utc_iso(),
        "finished_at": None,
        "current": None,
    }
    path = job_path(library_dir, job_id)
    write_json_atomic(path, record)

    # Detached so the job outlives the client that asked for it.
    subprocess.Popen(
        [sys.executable, "-m", "paperfetch_app.jobs", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=os.getcwd(),
    )
    return record


def get_job(library_dir: Path, job_id: str) -> dict[str, Any] | None:
    return read_json(job_path(library_dir.expanduser().resolve(), job_id), default=None)


def list_jobs(library_dir: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    directory = jobs_dir(library_dir.expanduser().resolve())
    if not directory.is_dir():
        return []
    records = [read_json(p, default=None) for p in directory.glob("*.json")]
    live = [r for r in records if isinstance(r, dict)]
    live.sort(key=lambda r: str(r.get("started_at", "")), reverse=True)
    return live[:limit]


def describe(record: dict[str, Any]) -> str:
    """One-line human/agent readable summary."""
    state = record.get("state")
    done, total = record.get("done", 0), record.get("total", 0)
    parts = [f"job {record.get('id')}: {state} ({done}/{total})"]
    if record.get("succeeded"):
        parts.append(f"{record['succeeded']} ok")
    if record.get("failed"):
        parts.append(f"{record['failed']} failed")
    if state not in TERMINAL_STATES and record.get("current"):
        parts.append(f"now: {record['current']}")
    return ", ".join(parts)


def _run(path: Path) -> int:
    """Execute a job file in this process, updating it after each paper."""
    record = read_json(path, default=None)
    if not isinstance(record, dict):
        return 1

    from .models import PaperInput
    from .runtime import default_fetch_options
    from .service import fetch_papers

    library_dir = Path(record["library_dir"])
    record["state"] = "running"
    write_json_atomic(path, record)

    try:
        options = default_fetch_options(
            library_dir,
            extractor=record.get("extractor", "auto"),
            out_dir=Path(record["out_dir"]) if record.get("out_dir") else None,
            project_files=record.get("project_files", "none"),
            marker_venv=Path(record["marker_venv"]) if record.get("marker_venv") else None,
        )
        # One paper at a time so progress is meaningful rather than all-or-nothing.
        for url in record["urls"]:
            record["current"] = url
            write_json_atomic(path, record)
            try:
                results = fetch_papers(library_dir, [PaperInput(slug="", title="", url=url, source="job")], options)
                result = results[0]
                entry = result.index_entry or {}
                row = {
                    "url": url,
                    "ok": bool(result.success),
                    "key": result.key,
                    "title": entry.get("title"),
                    "error": result.error,
                }
            except Exception as exc:
                row = {"url": url, "ok": False, "key": None, "title": None, "error": str(exc)}

            record["results"].append(row)
            record["done"] += 1
            record["succeeded" if row["ok"] else "failed"] += 1
            record["current"] = None
            write_json_atomic(path, record)

        record["state"] = "completed"
    except Exception as exc:
        record["state"] = "failed"
        record["error"] = str(exc)
    finally:
        record["finished_at"] = now_utc_iso()
        record["current"] = None
        write_json_atomic(path, record)
    return 0


if __name__ == "__main__":
    raise SystemExit(_run(Path(sys.argv[1])))
