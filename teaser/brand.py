"""Brand colour detection, with a restrained fallback.

A colour counts as "reliably identified" only when it is chromatic, not a stock Office
theme default, and (for raster detection) covers a meaningful share of the cover page.
Whatever is chosen is darkened until it reads well as heading text on white.
"""

from __future__ import annotations

import colorsys
import io
from collections import Counter

from PIL import Image

from teaser.ingest.models import Deck
from teaser.schema import Brand

#: Accent / dark colours shipped in stock Office themes; they say nothing about the brand.
OFFICE_DEFAULTS = frozenset({
    "#4472C4", "#5B9BD5", "#ED7D31", "#A5A5A5", "#FFC000", "#70AD47", "#4F81BD", "#C0504D",
    "#9BBB59", "#8064A2", "#1F497D", "#44546A", "#156082", "#E97132", "#196B24", "#0F9ED5",
    "#A02B93", "#4EA72E", "#0E2841", "#E8E8E8", "#335B74", "#1CADE4",
})
MIN_COVERAGE = 0.03


def _rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0, min(1, c)) * 255):02X}" for c in rgb)


def _luminance(rgb: tuple[float, float, float]) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_on_white(hex_color: str) -> float:
    """WCAG contrast ratio of the colour against white."""
    return 1.05 / (_luminance(_rgb(hex_color)) + 0.05)


def readable(hex_color: str, minimum: float = 5.0) -> str:
    """Darken a colour until it reaches ``minimum`` contrast against white."""
    h, lightness, s = colorsys.rgb_to_hls(*_rgb(hex_color))
    color = hex_color
    while contrast_on_white(color) < minimum and lightness > 0.05:
        lightness -= 0.03
        color = _hex(colorsys.hls_to_rgb(h, lightness, s))
    return color


def tint(hex_color: str, amount: float) -> str:
    """Mix a colour with white; ``amount`` 0 is the colour, 1 is white."""
    return _hex(tuple(c + (1 - c) * amount for c in _rgb(hex_color)))  # type: ignore[arg-type]


def _chromatic(hex_color: str) -> bool:
    _, lightness, s = colorsys.rgb_to_hls(*_rgb(hex_color))
    return s >= 0.35 and 0.15 <= lightness <= 0.75


def _dominant_cover_color(png: bytes) -> str | None:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.thumbnail((160, 160))
    flat = getattr(image, "get_flattened_data", image.getdata)  # getdata is deprecated in Pillow 12
    pixels = list(flat())
    buckets: Counter[tuple[int, int, int]] = Counter()
    for r, g, b in pixels:
        key = (r // 24, g // 24, b // 24)
        if _chromatic(_hex((r / 255, g / 255, b / 255))):
            buckets[key] += 1
    if not buckets:
        return None
    (key, count), = buckets.most_common(1)
    if count / len(pixels) < MIN_COVERAGE:
        return None
    # Average the actual pixels in the winning bucket for a true colour, not a bucket corner.
    members = [p for p in pixels if (p[0] // 24, p[1] // 24, p[2] // 24) == key]
    avg = tuple(sum(c[i] for c in members) / len(members) / 255 for i in range(3))
    return _hex(avg)  # type: ignore[arg-type]


def detect_brand(deck: Deck) -> Brand:
    """Choose brand colours from the deck theme or cover, or fall back to the default."""
    candidate: str | None = None
    source = ""
    for color in deck.theme_colors:
        if color.upper() not in OFFICE_DEFAULTS and _chromatic(color):
            candidate, source = color, "PowerPoint theme colour"
            break
    if candidate is None and deck.cover_png:
        try:
            candidate = _dominant_cover_color(deck.cover_png)
            source = "dominant colour on the cover page"
        except Exception:
            candidate = None
    if candidate is None:
        return Brand()
    primary = readable(candidate)
    return Brand(primary=primary, accent=candidate, detected=True, source=source)
