"""Font registration. Open Sans is bundled (SIL Open Font License); Helvetica is the fallback."""

from __future__ import annotations

import functools
from dataclasses import dataclass

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from teaser.config import ASSETS


@dataclass(frozen=True)
class Fonts:
    """Font names to use in paragraph styles."""

    regular: str
    bold: str
    italic: str
    semibold: str
    unicode: bool


@functools.cache
def register_fonts() -> Fonts:
    """Register Open Sans if its files are present; otherwise use the built-in Helvetica."""
    folder = ASSETS / "fonts"
    files = {
        "OpenSans": "OpenSans-Regular.ttf",
        "OpenSans-Bold": "OpenSans-Bold.ttf",
        "OpenSans-Italic": "OpenSans-Italic.ttf",
        "OpenSans-SemiBold": "OpenSans-SemiBold.ttf",
    }
    try:
        for name, file in files.items():
            pdfmetrics.registerFont(TTFont(name, str(folder / file)))
    except Exception:
        return Fonts("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-Bold", unicode=False)
    addMapping("OpenSans", 0, 0, "OpenSans")
    addMapping("OpenSans", 1, 0, "OpenSans-Bold")
    addMapping("OpenSans", 0, 1, "OpenSans-Italic")
    addMapping("OpenSans", 1, 1, "OpenSans-Bold")
    return Fonts("OpenSans", "OpenSans-Bold", "OpenSans-Italic", "OpenSans-SemiBold", unicode=True)
