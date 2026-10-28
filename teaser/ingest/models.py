"""The format-neutral deck model every ingestion path produces."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

_WS = re.compile(r"[ \t   ]+")
_BLANKS = re.compile(r"\n{3,}")

#: A page yielding fewer characters than this carries its content in pixels.
MIN_TEXT_CHARS = 20


def normalize_text(raw: str) -> str:
    """Collapse horizontal whitespace and blank-line runs, keeping line structure."""
    lines = [_WS.sub(" ", line).strip() for line in raw.replace("\r\n", "\n").split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def first_line(text: str) -> str | None:
    """The first non-empty line, used as a page title."""
    for line in text.split("\n"):
        if line.strip():
            return line.strip()[:200]
    return None


def flatten_table(rows: list[list[str | None]]) -> list[str]:
    """One line per table row, ``label: a | b | c``, so numbers keep their row labels."""
    lines: list[str] = []
    for row in rows:
        cells = [(c or "").strip().replace("\n", " ") for c in row]
        cells = [c for c in cells if c]
        if cells:
            lines.append(cells[0] if len(cells) == 1 else f"{cells[0]}: {' | '.join(cells[1:])}")
    return lines


def fmt_pages(pages: list[int], limit: int = 15) -> str:
    """Compact page list for messages: "1, 2, 3 (+4 more)"."""
    shown = ", ".join(str(p) for p in pages[:limit])
    return shown if len(pages) <= limit else f"{shown} (+{len(pages) - limit} more)"


class Page(BaseModel):
    """One page or slide."""

    index: int = Field(ge=1, description="1-based page/slide number used in every reference.")
    title: str | None = None
    text: str = ""
    text_source: Literal["text", "ocr", "mixed", "none"] = "text"
    image_count: int = 0
    has_table: bool = False

    @property
    def readable(self) -> bool:
        """Whether any text was recovered for this page."""
        return len(self.text) >= MIN_TEXT_CHARS


class Deck(BaseModel):
    """A pitch deck, normalized across source formats."""

    filename: str
    source_format: Literal["pdf", "pptx", "ppt"]
    pages: list[Page] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    theme_colors: list[str] = Field(
        default_factory=list, description="Hex colours from the PPTX theme, most prominent first."
    )
    cover_png: bytes | None = Field(default=None, description="Low-res render of page 1.")
    logo_candidates: list[bytes] = Field(
        default_factory=list, description="PNG images from the cover page, offered to the user."
    )

    @property
    def page_count(self) -> int:
        """Number of pages or slides."""
        return len(self.pages)

    @property
    def unreadable_pages(self) -> list[int]:
        """Pages for which no meaningful text could be recovered."""
        return [p.index for p in self.pages if not p.readable]

    def page(self, index: int) -> Page | None:
        """Page by 1-based number."""
        if 1 <= index <= len(self.pages):
            return self.pages[index - 1]
        return None

    def page_text(self, index: int) -> str:
        """Text of a page, or an empty string if the page does not exist."""
        page = self.page(index)
        return page.text if page else ""
