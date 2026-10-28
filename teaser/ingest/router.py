"""Format detection and dispatch, plus legacy .ppt conversion through LibreOffice."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from teaser.errors import IngestError, UnsupportedFormatError
from teaser.ingest.models import Deck
from teaser.ingest.pdf import load_pdf
from teaser.ingest.pptx import load_pptx

SUPPORTED = (".pdf", ".pptx", ".ppt")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
SOFFICE_TIMEOUT_S = 180
_SOFFICE_FALLBACKS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
)


def find_soffice() -> str | None:
    """Locate the LibreOffice CLI on PATH or in the usual install folders."""
    for name in ("soffice", "libreoffice"):
        if found := shutil.which(name):
            return found
    return next((c for c in _SOFFICE_FALLBACKS if Path(c).is_file()), None)


def sniff(data: bytes) -> str:
    """Identify the format from content, not the file extension."""
    head = data[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            import io

            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return "unknown"
        if "ppt/presentation.xml" in names:
            return "pptx"
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        return "unknown"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole2"  # legacy .ppt, but also .doc / .xls
    return "unknown"


def _convert_ppt(data: bytes, filename: str) -> bytes:
    soffice = find_soffice()
    if soffice is None:
        raise IngestError(
            f"{filename} is a legacy .ppt file. Converting it requires LibreOffice, which "
            f"was not found. Please open the deck in PowerPoint (or Keynote / Google Slides) "
            f"and save it as .pptx or export it as PDF, then upload that file."
        )
    with tempfile.TemporaryDirectory(prefix="teaser-ppt-") as tmp:
        source = Path(tmp) / "deck.ppt"
        source.write_bytes(data)
        try:
            result = subprocess.run(  # noqa: S603 - resolved binary, fixed arguments
                [soffice, "--headless", "--convert-to", "pptx", "--outdir", tmp, str(source)],
                capture_output=True,
                timeout=SOFFICE_TIMEOUT_S,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise IngestError(f"LibreOffice could not convert {filename}: {exc}") from exc
        produced = Path(tmp) / "deck.pptx"
        if result.returncode != 0 or not produced.is_file():
            raise IngestError(
                f"LibreOffice could not convert {filename}. The file may be corrupt or "
                f"password-protected; re-save it as .pptx or PDF and upload that instead."
            )
        return produced.read_bytes()


def load_deck(
    data: bytes, filename: str, *, password: str | None = None, enable_ocr: bool = True
) -> Deck:
    """Load an uploaded deck from bytes."""
    if not data:
        raise IngestError(f"{filename} is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(f"{filename} is larger than {MAX_UPLOAD_BYTES // 1_048_576} MB.")

    kind = sniff(data)
    suffix = Path(filename).suffix.lower()
    if kind == "pdf":
        return load_pdf(data, filename, password=password, enable_ocr=enable_ocr)
    if kind == "pptx":
        return load_pptx(data, filename, enable_ocr=enable_ocr)
    if kind == "ole2" and suffix == ".ppt":
        converted = _convert_ppt(data, filename)
        deck = load_pptx(converted, filename, enable_ocr=enable_ocr)
        deck.source_format = "ppt"
        deck.warnings.insert(0, "Legacy .ppt converted to .pptx with LibreOffice before reading.")
        return deck
    if kind == "ole2" and suffix == ".pptx":
        raise IngestError(
            f"{filename} appears to be encrypted (password-protected PowerPoint files are "
            f"stored in a different container). Remove the password in PowerPoint and re-upload."
        )
    if kind in ("docx", "xlsx", "ole2"):
        raise UnsupportedFormatError(
            f"{filename} looks like a Word/Excel document, not a pitch deck. "
            f"Supported formats: PDF, PPTX, PPT."
        )
    raise UnsupportedFormatError(
        f"{filename} is not a supported file type. Supported formats: PDF, PPTX, PPT "
        f"(legacy PPT needs LibreOffice)."
    )
