from pathlib import Path

import pymupdf
import pytest

from app.ingest.render import InvalidPDF, cached_page_image, file_sha256, page_count, render_page, validate_pdf


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(2):
        p = doc.new_page(width=200, height=200)
        p.draw_rect(pymupdf.Rect(0, 0, 100, 100), color=(0, 0, 0), fill=(0, 0, 0))  # black top-left quadrant
        p.insert_text((110, 150), f"page {i + 1}")
    path = tmp_path / "t.pdf"
    doc.save(path)
    return path


def test_page_count_and_validate(pdf):
    assert page_count(pdf) == 2
    assert validate_pdf(pdf) == 2


def test_validate_rejects_non_pdf(tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"PK\x03\x04 this is a docx")
    with pytest.raises(InvalidPDF):
        validate_pdf(bad)


def test_validate_rejects_empty(tmp_path):
    bad = tmp_path / "empty.pdf"
    bad.write_bytes(b"")
    with pytest.raises(InvalidPDF):
        validate_pdf(bad)


def test_render_png(pdf):
    data, media = render_page(pdf, 1, dpi=72)
    assert media == "image/png" and data.startswith(b"\x89PNG")


def test_rotate_is_clockwise(pdf):
    data, _ = render_page(pdf, 1, rotate=90, dpi=72)
    pix = pymupdf.Pixmap(data)
    w = pix.width
    assert pix.pixel(w - 10, 10)[0] < 50  # black quadrant moved top-left -> top-right
    assert pix.pixel(10, 10)[0] > 200


def test_cache_writes_once(pdf, tmp_path):
    h = file_sha256(pdf)
    a = cached_page_image(pdf, h, 2, tmp_path / "cache")
    files = list((tmp_path / "cache" / h / "pages").iterdir())
    assert len(files) == 1
    b = cached_page_image(pdf, h, 2, tmp_path / "cache")
    assert a == b
