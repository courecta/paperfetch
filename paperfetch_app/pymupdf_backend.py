from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ExtractionError
from .io_utils import ensure_dir


@dataclass(frozen=True)
class PymupdfResult:
    markdown_path: Path
    output_dir: Path


def pymupdf_available() -> bool:
    try:
        import pymupdf  # type: ignore  # noqa: F401

        return True
    except Exception:
        pass
    try:
        import fitz  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def extract_with_pymupdf(pdf_path: Path, work_dir: Path, *, dpi: int = 180) -> PymupdfResult:
    """Fast CPU extraction used when marker is unavailable or fails."""
    ensure_dir(work_dir)
    stem = pdf_path.stem
    markdown_path = work_dir / stem / f"{stem}.md"
    images_dir = work_dir / stem / f"{stem}_images"
    ensure_dir(markdown_path.parent)

    try:
        import pymupdf4llm  # type: ignore
    except Exception:
        pymupdf4llm = None

    if pymupdf4llm is not None:
        ensure_dir(images_dir)
        markdown = pymupdf4llm.to_markdown(
            str(pdf_path),
            write_images=True,
            image_path=str(images_dir),
            image_format="png",
            dpi=dpi,
        )
        markdown_path.write_text(markdown, encoding="utf-8")
        return PymupdfResult(markdown_path=markdown_path, output_dir=markdown_path.parent)

    markdown = _extract_with_fitz(pdf_path, images_dir, dpi=dpi)
    markdown_path.write_text(markdown, encoding="utf-8")
    return PymupdfResult(markdown_path=markdown_path, output_dir=markdown_path.parent)


def _extract_with_fitz(pdf_path: Path, images_dir: Path, *, dpi: int) -> str:
    try:
        import pymupdf as fitz  # type: ignore
    except Exception:
        try:
            import fitz  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            raise ExtractionError(
                "Neither marker nor PyMuPDF is available for PDF extraction",
                hint="Install pymupdf (pip install pymupdf) or marker-pdf.",
            ) from exc

    ensure_dir(images_dir)
    parts: list[str] = []
    doc = fitz.open(str(pdf_path))
    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            parts.append(page.get_text("text").strip())
            seen: set[int] = set()
            for image_index, image in enumerate(page.get_images(full=True)):
                xref = image[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    name = f"page-{page_index + 1:03d}-img-{image_index + 1}.png"
                    pix.save(str(images_dir / name))
                    parts.append(f"![{name}]({images_dir.name}/{name})")
                except Exception:
                    continue
    finally:
        doc.close()
    return "\n\n".join(part for part in parts if part)
