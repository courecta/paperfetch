from __future__ import annotations

import re
from typing import Any


def _sanitize_bibtex_key(text: str) -> str:
    """Create a safe BibTeX citation key from a string."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "paper"


def _generate_bibtex(entry: dict[str, Any]) -> str | None:
    """Generate a minimal BibTeX entry from index metadata."""
    title = entry.get("title", "")
    authors = entry.get("authors")
    year = entry.get("year")
    url = entry.get("source_url", "")
    kind = entry.get("identity_kind", "")
    value = entry.get("identity_value", "")
    doi = entry.get("doi")

    if not title:
        return None

    # Determine entry type
    if kind == "arxiv":
        entry_type = "misc"
    elif doi:
        entry_type = "article"
    else:
        entry_type = "misc"

    # Build citation key
    first_author = "unknown"
    if authors and isinstance(authors, list) and authors[0]:
        first_author = _sanitize_bibtex_key(authors[0].split()[-1])
    year_str = str(year) if year else "0000"
    key = f"{first_author}{year_str}"

    lines: list[str] = [f"@{entry_type}{{{key},"]
    lines.append(f"  title = {{{title}}},")

    if authors and isinstance(authors, list):
        author_str = " and ".join(authors)
        lines.append(f"  author = {{{author_str}}},")

    if year:
        lines.append(f"  year = {{{year}}},")

    if kind == "arxiv" and value:
        lines.append(f"  eprint = {{{value}}},")
        lines.append(f"  archivePrefix = {{arXiv}},")
        primary = entry.get("primary_category")
        if primary:
            lines.append(f"  primaryClass = {{{primary}}},")

    if doi:
        lines.append(f"  doi = {{{doi}}},")

    if url:
        lines.append(f"  url = {{{url}}},")

    lines.append("}")
    return "\n".join(lines)


def export_bibtex(entries: list[dict[str, Any]]) -> str:
    """Export a list of index entries as a combined BibTeX string."""
    outputs: list[str] = []
    for entry in entries:
        bib = entry.get("bibtex")
        if bib and isinstance(bib, str):
            outputs.append(bib.strip())
        else:
            generated = _generate_bibtex(entry)
            if generated:
                outputs.append(generated)
    return "\n\n".join(outputs) + "\n"
