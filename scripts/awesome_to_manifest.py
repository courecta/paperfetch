#!/usr/bin/env python
"""Turn a curated markdown paper list into a paperfetch manifest.

"Awesome" lists are the usual way a field's recent literature is tracked, and
they are all shaped the same way: a bullet per paper, a title, then one or more
[[label]](url) links. This pulls out the best fetchable link per entry.

    python scripts/awesome_to_manifest.py awesome.md --section "CVPR 2026" -o papers.csv
    python scripts/awesome_to_manifest.py awesome.md --section "CVPR 2026" --section "ICCV 2025"

Entries whose only link is a landing page with no retrievable PDF (a
conference virtual-site poster, say) are reported and skipped rather than
silently dropped.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

LINK = re.compile(r"\[\[([^\]]+)\]\]\((https?://[^)]+)\)")
BULLET = re.compile(r"^\s*[-+*]\s+(.*)$")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")

# Links that are never the paper itself.
IGNORED_LABELS = {"code", "data", "dataset", "project", "page", "video", "demo", "website", "homepage"}
IGNORED_HOSTS = {"github.com", "gitlab.com", "huggingface.co", "youtube.com", "youtu.be"}

# Hosts we can actually resolve to a PDF, best first.
PREFERRED_HOSTS = (
    "arxiv.org",
    "openreview.net",
    "aclanthology.org",
    "proceedings.mlr.press",
    "openaccess.thecvf.com",
    "ncbi.nlm.nih.gov",
    "doi.org",
    "biorxiv.org",
    "medrxiv.org",
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _rank(url: str) -> int:
    host = _host(url)
    for index, preferred in enumerate(PREFERRED_HOSTS):
        if host.endswith(preferred):
            return index
    return len(PREFERRED_HOSTS)


def _slug(title: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "paper"
    slug, n = base, 2
    while slug in used:
        slug = f"{base}-{n}"
        n += 1
    used.add(slug)
    return slug


def parse(markdown: str, sections: list[str]) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    wanted = {s.strip().lower() for s in sections}
    current = ""
    rows: list[dict[str, str]] = []
    skipped: list[tuple[str, str]] = []
    used: set[str] = set()

    for line in markdown.splitlines():
        heading = HEADING.match(line)
        if heading:
            current = heading.group(1).strip().lower()
            continue
        if wanted and current not in wanted:
            continue
        bullet = BULLET.match(line)
        if not bullet:
            continue

        body = bullet.group(1)
        links = LINK.findall(body)
        if not links:
            continue
        title = LINK.sub("", body).strip(" .;:-–—")
        if not title:
            continue

        candidates = [
            url
            for label, url in links
            if label.strip().lower() not in IGNORED_LABELS and not any(_host(url).endswith(h) for h in IGNORED_HOSTS)
        ]
        if not candidates:
            skipped.append((title, "only code/data links"))
            continue

        candidates.sort(key=_rank)
        best = candidates[0]
        if _rank(best) == len(PREFERRED_HOSTS):
            skipped.append((title, f"no retrievable source ({_host(best)})"))
            continue
        rows.append({"url": best, "title": title, "slug": _slug(title, used)})

    return rows, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("markdown", type=Path, help="Local markdown file (curl the raw README first)")
    parser.add_argument("--section", action="append", default=[], help="Heading to include; repeatable")
    parser.add_argument("-o", "--output", type=Path, help="Manifest CSV path (default: stdout)")
    args = parser.parse_args()

    rows, skipped = parse(args.markdown.read_text(encoding="utf-8"), args.section)

    if skipped:
        print(f"skipped {len(skipped)} entries with no fetchable source:", file=sys.stderr)
        for title, why in skipped:
            print(f"  - {title[:70]}: {why}", file=sys.stderr)

    handle = args.output.open("w", newline="", encoding="utf-8") if args.output else sys.stdout
    try:
        writer = csv.DictWriter(handle, fieldnames=["url", "title", "slug"])
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if args.output:
            handle.close()
            print(f"wrote {len(rows)} papers to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
