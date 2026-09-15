#!/usr/bin/env python
"""Extraction-quality regression harness.

The unit suite cannot see the failures that matter here. Every one of the real
extraction bugs found so far -- tables arriving with zero rows, sidecars never
written, heading levels off by up to three, an empty stub scoring 1.0 coverage
-- passed a fully green test run and was only visible when real papers went
through the real pipeline. This runs that corpus and compares the result
against a recorded baseline.

    uv run python scripts/validate.py                 # check against baseline
    uv run python scripts/validate.py --update        # record a new baseline
    uv run python scripts/validate.py --keep artifacts/  # keep the bundles

Needs network, a GPU and the marker venv, so it is a deliberate manual/nightly
step rather than part of CI.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "validation" / "corpus.csv"
BASELINE = ROOT / "validation" / "baseline.json"

# Counts are layout-model output, so they drift a little between marker
# releases. Fail on collapse, not on noise.
TOLERANCE = 0.80


def measure_bundle(bundle: Path) -> dict[str, Any]:
    meta = json.loads((bundle / "meta.json").read_text())
    document = json.loads((bundle / "document.json").read_text())
    blocks = document.get("blocks") or []

    kinds: dict[str, int] = {}
    for block in blocks:
        kind = str(block.get("kind"))
        kinds[kind] = kinds.get(kind, 0) + 1

    figures = json.loads((bundle / "figures.json").read_text()).get("figures") or []
    tables_path = bundle / "tables.json"
    tables = json.loads(tables_path.read_text()).get("tables") or [] if tables_path.exists() else []

    table_rows = 0
    for block in blocks:
        if block.get("kind") == "table":
            table_rows += len((block.get("table") or {}).get("rows") or [])

    coverage = meta.get("coverage") or {}
    return {
        "extractor": meta.get("source_kind"),
        "blocks": len(blocks),
        "headings": kinds.get("heading", 0),
        "equations": kinds.get("equation", 0),
        "figures": len(figures),
        "captioned_figures": sum(1 for f in figures if f.get("caption")),
        "tables": len(tables),
        "table_rows": table_rows,
        "crops": len(list((bundle / "crops").glob("*.png"))) if (bundle / "crops").is_dir() else 0,
        "pages": len(list((bundle / "pages").glob("*.png"))) if (bundle / "pages").is_dir() else 0,
        "coverage_ok": bool(coverage.get("ok")),
        "ir_available": bool(coverage.get("ir_available")),
    }


def run_corpus(library: Path, extractor: str, verbose: bool) -> dict[str, dict[str, Any]]:
    cmd = [
        sys.executable,
        "-m",
        "paperfetch_app",
        "fetch",
        "--manifest",
        str(CORPUS),
        "--library-dir",
        str(library),
        "--extractor",
        extractor,
        "--workers",
        "1",
    ]
    print(f"$ {' '.join(cmd)}\n", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=not verbose)
    if proc.returncode != 0 and not verbose:
        print(proc.stdout or "", file=sys.stderr)
        print(proc.stderr or "", file=sys.stderr)

    results: dict[str, dict[str, Any]] = {}
    for meta_path in sorted(library.glob("*/meta.json")):
        bundle = meta_path.parent
        try:
            meta = json.loads(meta_path.read_text())
            results[str(meta.get("title") or bundle.name)] = measure_bundle(bundle)
        except Exception as exc:
            results[bundle.name] = {"error": str(exc)}
    return results


NUMERIC = ("blocks", "headings", "figures", "captioned_figures", "tables", "table_rows", "crops", "pages")


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for title, expected in baseline.items():
        got = current.get(title)
        if got is None:
            failures.append(f"{title}: missing from this run")
            continue
        if expected.get("ir_available") and not got.get("ir_available"):
            failures.append(f"{title}: lost structured IR")
        for field in NUMERIC:
            want, have = expected.get(field, 0), got.get(field, 0)
            if want and have < want * TOLERANCE:
                failures.append(f"{title}: {field} {have} < {want} baseline (tolerance {TOLERANCE:.0%})")
    for title in current:
        if title not in baseline:
            print(f"note: {title} is new and has no baseline")
    return failures


def render(results: dict[str, Any]) -> None:
    head = f"{'paper':28}{'via':11}{'blocks':8}{'head':6}{'eq':5}{'figs':6}{'cap':5}{'tbl':5}{'rows':6}{'crop':6}"
    print(head)
    print("-" * len(head))
    for title, row in sorted(results.items()):
        if "error" in row:
            print(f"{title[:26]:28}ERROR {row['error'][:40]}")
            continue
        print(
            f"{title[:26]:28}{str(row['extractor']):11}{row['blocks']:<8}{row['headings']:<6}"
            f"{row['equations']:<5}{row['figures']:<6}{row['captioned_figures']:<5}"
            f"{row['tables']:<5}{row['table_rows']:<6}{row['crops']:<6}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="Record this run as the new baseline")
    parser.add_argument("--extractor", default="auto", choices=["auto", "arxiv_html", "marker"])
    parser.add_argument("--keep", type=Path, help="Keep the produced bundles in this directory")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    library = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="paperfetch-validate-"))
    library.mkdir(parents=True, exist_ok=True)
    try:
        results = run_corpus(library, args.extractor, args.verbose)
        print()
        render(results)
        print()

        if args.update:
            BASELINE.parent.mkdir(parents=True, exist_ok=True)
            BASELINE.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
            print(f"baseline written to {BASELINE.relative_to(ROOT)} ({len(results)} papers)")
            return 0

        if not BASELINE.exists():
            print("no baseline recorded; run with --update first", file=sys.stderr)
            return 1

        failures = compare(results, json.loads(BASELINE.read_text()))
        if failures:
            print("REGRESSIONS:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print(f"OK: {len(results)} papers match baseline")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(library, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
