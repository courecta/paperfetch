from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

from .identity import build_identity
from .io_utils import ensure_dir, safe_slug
from .models import PaperInput


def parse_inline_spec(spec: str) -> PaperInput:
    parts = [p.strip() for p in spec.split("|")]
    if len(parts) == 1:
        identity = build_identity(parts[0])
        slug = safe_slug(Path(identity.normalized_url).stem or "paper")
        title = slug.replace("-", " ")
        return PaperInput(slug=slug, title=title, url=identity.normalized_url, source="inline")

    if len(parts) == 2:
        title, url = parts
        identity = build_identity(url)
        return PaperInput(slug=safe_slug(title), title=title, url=identity.normalized_url, source="inline")

    slug, title, url = parts[0], parts[1], parts[2]
    identity = build_identity(url)
    return PaperInput(
        slug=safe_slug(slug),
        title=title,
        url=identity.normalized_url,
        source="inline",
    )


def _coerce_paper_dict(obj: dict[str, object], source: str) -> PaperInput | None:
    url = obj.get("url")
    if not isinstance(url, str) or not url.strip():
        return None

    title_val = obj.get("title", obj.get("name", "paper"))
    if not isinstance(title_val, str):
        title_val = str(title_val)

    slug_val = obj.get("slug")
    if not isinstance(slug_val, str) or not slug_val.strip():
        slug_val = safe_slug(title_val)

    identity = build_identity(url)
    return PaperInput(
        slug=safe_slug(slug_val),
        title=title_val.strip() or slug_val,
        url=identity.normalized_url,
        source=source,
    )


def parse_manifest(path: Path) -> list[PaperInput]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix == ".json":
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise ValueError(f"JSON manifest must be a list: {path}")
        out: list[PaperInput] = []
        for item in payload:
            if isinstance(item, str):
                out.append(parse_inline_spec(item))
            elif isinstance(item, dict):
                parsed = _coerce_paper_dict(item, source=str(path))
                if parsed:
                    out.append(parsed)
        return out

    if suffix == ".jsonl":
        out: list[PaperInput] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, str):
                out.append(parse_inline_spec(item))
            elif isinstance(item, dict):
                parsed = _coerce_paper_dict(item, source=str(path))
                if parsed:
                    out.append(parsed)
        return out

    if suffix in {".csv", ".tsv"}:
        delimiter = "," if suffix == ".csv" else "\t"
        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
        out: list[PaperInput] = []
        for row in reader:
            parsed = _coerce_paper_dict(row, source=str(path))
            if parsed:
                out.append(parsed)
        return out

    out: list[PaperInput] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(parse_inline_spec(line))
    return out


def _ast_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def parse_legacy_python(path: Path) -> list[PaperInput]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[PaperInput] = []

    candidate_values: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "PAPERS" for t in node.targets):
                candidate_values.append(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "PAPERS" and node.value is not None:
                candidate_values.append(node.value)

    for candidate in candidate_values:
        if not isinstance(candidate, (ast.List, ast.Tuple)):
            continue

        for elem in candidate.elts:
            if not isinstance(elem, ast.Call):
                continue

            func_name = elem.func.id if isinstance(elem.func, ast.Name) else ""
            if func_name != "Paper":
                continue

            slug = _ast_string(elem.args[0]) if len(elem.args) > 0 else None
            title = _ast_string(elem.args[1]) if len(elem.args) > 1 else None
            url = _ast_string(elem.args[2]) if len(elem.args) > 2 else None

            for keyword in elem.keywords:
                if keyword.arg == "slug" and not slug:
                    slug = _ast_string(keyword.value)
                if keyword.arg in {"name", "title"} and not title:
                    title = _ast_string(keyword.value)
                if keyword.arg == "url" and not url:
                    url = _ast_string(keyword.value)

            if not url:
                continue
            if not slug:
                slug = safe_slug(title or "paper")
            if not title:
                title = slug.replace("-", " ")

            identity = build_identity(url)
            out.append(
                PaperInput(
                    slug=safe_slug(slug),
                    title=title,
                    url=identity.normalized_url,
                    source=str(path),
                )
            )

    return out


def collect_inputs(
    manifests: list[str],
    from_python: list[str],
    inline_specs: list[str],
    urls: list[str],
) -> list[PaperInput]:
    papers: list[PaperInput] = []

    for manifest in manifests:
        papers.extend(parse_manifest(Path(manifest).expanduser().resolve()))

    for py_file in from_python:
        papers.extend(parse_legacy_python(Path(py_file).expanduser().resolve()))

    for spec in inline_specs:
        papers.append(parse_inline_spec(spec))

    for url in urls:
        identity = build_identity(url)
        stem = Path(identity.normalized_url).stem
        slug = safe_slug(stem or "paper")
        papers.append(
            PaperInput(
                slug=slug,
                title=slug.replace("-", " "),
                url=identity.normalized_url,
                source="--url",
            )
        )

    if not papers:
        raise ValueError("No papers were provided. Use --manifest, --from-python, --paper, or --url.")

    deduped: list[PaperInput] = []
    seen: set[str] = set()
    for paper in papers:
        identity = build_identity(paper.url)
        if identity.fingerprint in seen:
            continue
        seen.add(identity.fingerprint)
        deduped.append(paper)

    return deduped


def init_manifest(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")

    sample = """# paperfetch text manifest
# Format options per non-comment line:
# 1) slug|title|url
# 2) title|url
# 3) url

kan-ad|KAN-AD|https://arxiv.org/abs/2411.00278
CATCH|https://arxiv.org/abs/2410.12261
https://openreview.net/forum?id=YQ8tUwlthw
"""
    ensure_dir(path.parent)
    path.write_text(sample, encoding="utf-8")
