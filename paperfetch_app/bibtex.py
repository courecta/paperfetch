from __future__ import annotations

import re
from typing import Any

ARXIV_KINDS = {"arxiv"}


def _sanitize_bibtex_key(text: str) -> str:
    """Create a safe BibTeX citation key from a string."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "paper"


def _balance_braces(value: str) -> str:
    depth = 0
    out: list[str] = []
    for char in value:
        if char == "{":
            depth += 1
            out.append(char)
        elif char == "}":
            if depth == 0:
                continue
            depth -= 1
            out.append(char)
        else:
            out.append(char)
    if depth:
        out.append("}" * depth)
    return "".join(out)


def _base_key(metadata: dict[str, Any]) -> str:
    authors = metadata.get("authors")
    first_author = "unknown"
    if isinstance(authors, list) and authors and authors[0]:
        first_author = _sanitize_bibtex_key(str(authors[0]).split()[-1])
    year = metadata.get("year")
    year_str = str(year) if year else "0000"
    title = str(metadata.get("title") or "")
    stop = {
        "a",
        "an",
        "the",
        "on",
        "of",
        "for",
        "and",
        "in",
        "to",
        "with",
        "at",
        "by",
        "from",
    }
    words = [re.sub(r"[^a-zA-Z0-9]", "", word) for word in title.split()]
    keyword = next((word for word in words if word and word.lower() not in stop), "")
    return _sanitize_bibtex_key(f"{first_author}{year_str}{keyword}")


def unique_bibkey(metadata: dict[str, Any], used_keys: set[str] | None = None) -> str:
    used = used_keys if used_keys is not None else set()
    base = _base_key(metadata)
    if base not in used:
        used.add(base)
        return base
    suffix = ord("a")
    while True:
        candidate = f"{base}{chr(suffix)}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1
        if suffix > ord("z"):
            counter = 2
            while f"{base}{counter}" in used:
                counter += 1
            candidate = f"{base}{counter}"
            used.add(candidate)
            return candidate


def generate_bibtex(metadata: dict[str, Any], used_keys: set[str] | None = None) -> str:
    title = str(metadata.get("title") or "").strip()
    if not title:
        return ""
    authors = metadata.get("authors")
    year = metadata.get("year")
    doi = metadata.get("doi")
    kind = str(metadata.get("identity_kind") or "")
    arxiv_id = metadata.get("arxiv_id") or (metadata.get("identity_value") if kind in ARXIV_KINDS else None)
    venue = metadata.get("venue")
    url = metadata.get("source_url") or metadata.get("url")

    entry_type = "article" if (venue or doi) else "misc"
    key = unique_bibkey(metadata, used_keys)
    lines = [f"@{entry_type}{{{key},"]
    lines.append(f"  title = {{{_balance_braces(title)}}},")
    if isinstance(authors, list) and authors:
        author_str = " and ".join(_balance_braces(str(author)) for author in authors)
        lines.append(f"  author = {{{author_str}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if venue:
        lines.append(f"  journal = {{{_balance_braces(str(venue))}}},")
    if arxiv_id:
        lines.append(f"  eprint = {{{arxiv_id}}},")
        lines.append("  archivePrefix = {arXiv},")
        primary = metadata.get("primary_category")
        if primary:
            lines.append(f"  primaryClass = {{{primary}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if url:
        lines.append(f"  url = {{{url}}},")
    lines.append("}")
    return "\n".join(lines)


def _generate_bibtex(entry: dict[str, Any]) -> str | None:
    """Generate a BibTeX entry from index metadata (legacy API)."""
    generated = generate_bibtex(entry)
    return generated or None


def export_bibtex(entries: list[dict[str, Any]]) -> str:
    outputs: list[str] = []
    used_keys: set[str] = set()
    for entry in entries:
        bib = entry.get("bibtex")
        if bib and isinstance(bib, str):
            outputs.append(bib.strip())
            match = re.search(r"@\w+\{([^,]+),", bib)
            if match:
                used_keys.add(match.group(1).strip())
        else:
            generated = generate_bibtex(entry, used_keys)
            if generated:
                outputs.append(generated)
    if not outputs:
        return "\n"
    return "\n\n".join(outputs) + "\n"
