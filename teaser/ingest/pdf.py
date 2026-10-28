"""PDF ingestion with PyMuPDF: text, tables, encryption, scanned-page detection, OCR."""

from __future__ import annotations

import pymupdf

from teaser.errors import IngestError, PasswordRequiredError
from teaser.ingest.models import Deck, Page, first_line, flatten_table, fmt_pages, normalize_text
from teaser.ingest.ocr import ocr_available, ocr_image

OCR_DPI = 200
COVER_DPI = 60


def _tables(page: pymupdf.Page) -> list[str]:
    try:
        found = page.find_tables()
    except Exception:  # table detection is best-effort
        return []
    lines: list[str] = []
    for table in found.tables:
        try:
            lines.extend(flatten_table(table.extract()))
        except Exception:
            continue
    return lines


def _cover_images(document: pymupdf.Document) -> list[bytes]:
    """Embedded images on page 1 large enough to be a logo, as PNG."""
    out: list[bytes] = []
    for xref, *_ in document[0].get_images(full=True)[:6]:
        try:
            pix = pymupdf.Pixmap(document, xref)
            if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            if 40 <= pix.width <= 2000 and 20 <= pix.height <= 1200:
                out.append(pix.tobytes("png"))
        except Exception:
            continue
    return out


def load_pdf(data: bytes, filename: str, *, password: str | None, enable_ocr: bool) -> Deck:
    """Read a PDF from bytes."""
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise IngestError(f"{filename} is not a readable PDF ({exc}).") from exc

    with document:
        if document.needs_pass:
            if not password:
                raise PasswordRequiredError(
                    f"{filename} is password-protected. Enter the password to open it, or "
                    f"upload an unprotected copy."
                )
            if not document.authenticate(password):
                raise PasswordRequiredError(f"The password for {filename} was not accepted.")
        if document.page_count == 0:
            raise IngestError(f"{filename} contains no pages.")

        can_ocr = enable_ocr and ocr_available()
        warnings: list[str] = []
        pages: list[Page] = []
        for number in range(1, document.page_count + 1):
            page = document[number - 1]
            text = page.get_text("text", sort=True) or ""
            table_lines = _tables(page)
            if table_lines:
                text = f"{text}\n[table]\n" + "\n".join(table_lines)
            text = normalize_text(text)
            source = "text"
            images = len(page.get_images())
            if len(text) < 20 and can_ocr:
                pix = page.get_pixmap(dpi=OCR_DPI, alpha=False)
                recognised = normalize_text(ocr_image(pix.tobytes("png")))
                if recognised:
                    text = normalize_text(f"{text}\n{recognised}") if text else recognised
                    source = "ocr" if len(text) == len(recognised) else "mixed"
            if not text:
                source = "none"
            pages.append(
                Page(
                    index=number,
                    title=first_line(text),
                    text=text,
                    text_source=source,
                    image_count=images,
                    has_table=bool(table_lines),
                )
            )

        cover = document[0].get_pixmap(dpi=COVER_DPI, alpha=False).tobytes("png")
        logos = _cover_images(document)

    deck = Deck(
        filename=filename,
        source_format="pdf",
        pages=pages,
        warnings=warnings,
        cover_png=cover,
        logo_candidates=logos,
    )
    _warn_unreadable(deck, can_ocr=can_ocr, ocr_requested=enable_ocr)
    return deck


def _warn_unreadable(deck: Deck, *, can_ocr: bool, ocr_requested: bool) -> None:
    unreadable = deck.unreadable_pages
    ocr_pages = [p.index for p in deck.pages if p.text_source in ("ocr", "mixed")]
    if ocr_pages:
        deck.warnings.append(
            f"Text on page(s) {fmt_pages(ocr_pages)} was recovered by OCR and may contain "
            f"recognition errors. Check figures from these pages carefully."
        )
    if not unreadable:
        return
    if len(unreadable) == deck.page_count:
        lead = "No text could be read from this deck; it appears to be scanned images."
    else:
        lead = f"No text could be read from page(s) {fmt_pages(unreadable)} (image-only content)."
    if can_ocr:
        tail = "OCR was attempted but found no legible text there."
    elif ocr_requested:
        tail = "OCR is enabled but no OCR engine is installed (pip install rapidocr-onnxruntime)."
    else:
        tail = "Enable OCR in the sidebar to try reading them."
    deck.warnings.append(f"{lead} {tail} Content on those pages will not appear in the teaser.")
