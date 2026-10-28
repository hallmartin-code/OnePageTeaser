"""Ingestion: formats, references, and clear failures."""

from __future__ import annotations

import io

import pymupdf
import pytest
from docx import Document

from teaser.errors import IngestError, PasswordRequiredError, UnsupportedFormatError
from teaser.ingest import load_deck, router


def _pdf(*texts: str, image_only: bool = False) -> bytes:
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page()
        if image_only:
            page.draw_rect(pymupdf.Rect(50, 50, 300, 300), fill=(0.2, 0.4, 0.6))
        else:
            page.insert_text((72, 72), text)
    return doc.tobytes()


def test_pdf_pages_keep_their_numbers(sample_pdf_deck) -> None:  # type: ignore[no-untyped-def]
    assert sample_pdf_deck.page_count == 12
    assert sample_pdf_deck.page(5).text.startswith("Market Opportunity")
    assert "TAM: $22.4B" in sample_pdf_deck.page_text(5)


def test_pptx_tables_are_flattened_with_row_labels(sample_pptx_deck) -> None:  # type: ignore[no-untyped-def]
    page = sample_pptx_deck.page(4)
    assert page.has_table
    assert "HT-101: Ulcerative colitis | IND-enabling" in page.text
    assert sample_pptx_deck.theme_colors[0] == "#0B6E69"


def test_image_only_slide_reported_when_ocr_off(sample_pptx_deck) -> None:  # type: ignore[no-untyped-def]
    assert 11 in sample_pptx_deck.unreadable_pages
    assert any("11" in w and "OCR" in w for w in sample_pptx_deck.warnings)


def test_scanned_pdf_is_reported() -> None:
    deck = load_deck(_pdf("", "", image_only=True), "scan.pdf", enable_ocr=False)
    assert deck.unreadable_pages == [1, 2]
    assert any("scanned" in w for w in deck.warnings)


def test_password_protected_pdf() -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Secret deck with enough text to read")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw123", owner_pw="own")
    with pytest.raises(PasswordRequiredError):
        load_deck(data, "locked.pdf")
    with pytest.raises(PasswordRequiredError, match="not accepted"):
        load_deck(data, "locked.pdf", password="wrong")
    deck = load_deck(data, "locked.pdf", password="pw123")
    assert "Secret deck" in deck.page_text(1)


def test_corrupt_pdf() -> None:
    with pytest.raises(IngestError):
        load_deck(b"%PDF-1.7 this is not really a pdf", "broken.pdf")


def test_unsupported_formats() -> None:
    with pytest.raises(UnsupportedFormatError, match="not a supported"):
        load_deck(b"hello world", "notes.txt")
    buf = io.BytesIO()
    Document().save(buf)
    with pytest.raises(UnsupportedFormatError, match="Word/Excel"):
        load_deck(buf.getvalue(), "memo.docx")
    with pytest.raises(IngestError, match="empty"):
        load_deck(b"", "empty.pdf")


def test_legacy_ppt_without_libreoffice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(router, "find_soffice", lambda: None)
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    with pytest.raises(IngestError, match="save it as .pptx"):
        load_deck(ole, "old.ppt")


def test_extension_is_not_trusted(sample_pdf_deck) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    data = (Path(__file__).parent.parent / "samples" / "sample_deck.pdf").read_bytes()
    deck = load_deck(data, "misnamed.pptx", enable_ocr=False)
    assert deck.source_format == "pdf"
