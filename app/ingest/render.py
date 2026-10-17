"""PDF page rendering with PyMuPDF. Faxes are black-and-white, so pages render in grayscale."""
import hashlib
from pathlib import Path

import pymupdf

MAX_PNG_BYTES = 4_000_000


class InvalidPDF(ValueError):
    """The upload is not a readable, unencrypted PDF with at least one page."""


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_pdf(path: Path) -> int:
    try:
        with pymupdf.open(path, filetype="pdf") as doc:
            if doc.needs_pass:
                raise InvalidPDF("PDF is password protected")
            if doc.page_count < 1:
                raise InvalidPDF("PDF has no pages")
            return doc.page_count
    except InvalidPDF:
        raise
    except Exception as e:
        raise InvalidPDF(f"Not a readable PDF: {e}") from e


def page_count(path: Path) -> int:
    with pymupdf.open(path, filetype="pdf") as doc:
        return doc.page_count


def render_page(pdf_path: Path, page: int, rotate: int = 0, dpi: int = 150) -> tuple[bytes, str]:
    """Render 1-indexed `page`, rotated `rotate` degrees clockwise. Returns (bytes, media_type)."""
    with pymupdf.open(pdf_path, filetype="pdf") as doc:
        zoom = dpi / 72
        matrix = pymupdf.Matrix(zoom, zoom).prerotate(rotate)
        pix = doc[page - 1].get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY)
        png = pix.tobytes("png")
        if len(png) <= MAX_PNG_BYTES:
            return png, "image/png"
        return pix.tobytes("jpeg", jpg_quality=85), "image/jpeg"


def cached_page_image(pdf_path: Path, packet_hash: str, page: int, cache_root: Path,
                      rotate: int = 0) -> tuple[bytes, str]:
    folder = cache_root / packet_hash / "pages"
    folder.mkdir(parents=True, exist_ok=True)
    for ext, media in (("png", "image/png"), ("jpg", "image/jpeg")):
        f = folder / f"{page}_r{rotate}.{ext}"
        if f.exists():
            return f.read_bytes(), media
    data, media = render_page(pdf_path, page, rotate)
    ext = "png" if media == "image/png" else "jpg"
    (folder / f"{page}_r{rotate}.{ext}").write_bytes(data)
    return data, media
