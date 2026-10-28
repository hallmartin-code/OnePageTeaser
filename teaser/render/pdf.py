"""One-page US Letter teaser PDF with ReportLab.

Layout: a header (logo, company name, tagline, website), two body columns, the disclaimer,
and an optional TEN Capital footer. Sections are kept whole and assigned to columns in
reading order at the split point that balances the columns best.

Fitting is measured, not hoped for: every section is wrapped at the column width for each
candidate body size (10.5 pt down to 9 pt) and the largest size whose tallest column fits
wins. If nothing fits at 9 pt, ``LayoutOverflowError`` reports by how much, and nothing is
drawn. The drawn PDF is then re-opened to confirm it has exactly one page.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

import pymupdf
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, Frame, Paragraph, Spacer, Table, TableStyle

from teaser.brand import tint
from teaser.config import ASSETS
from teaser.errors import LayoutOverflowError, RenderError
from teaser.render.fonts import Fonts, register_fonts
from teaser.schema import TEMPLATE, ItemRole, SectionKey, TeaserDraft, TeaserItem

# Page geometry and type sizes come from templates/teaser_template.json.
_PAGE, _TYPE = TEMPLATE.page, TEMPLATE.typography
PAGE_W, PAGE_H = letter
MARGIN_X = _PAGE.margins_in.left * inch
MARGIN_TOP = _PAGE.margins_in.top * inch
MARGIN_BOTTOM = _PAGE.margins_in.bottom * inch
GUTTER = _PAGE.column_gutter_in * inch
COL_W = (PAGE_W - 2 * MARGIN_X - GUTTER) / 2
BODY_SIZES = _TYPE.body_sizes
MIN_BODY_SIZE = _TYPE.body_pt.min
LINE_HEIGHT = _TYPE.line_height
DISCLAIMER_SIZE = _TYPE.disclaimer_pt
TEN_LOGO = ASSETS / "TEN_Capital_logo_footer.png"

#: Reading order, as listed in the template.
ORDER = [SectionKey(k) for k in TEMPLATE.section_keys]

_INK = HexColor("#1E2329")
_MUTED = HexColor("#555C66")


@dataclass
class RenderOptions:
    """Choices made in the interface."""

    ten_footer: bool = True
    include_logo: bool = False
    compiled_on: dt.date = field(default_factory=dt.date.today)


@dataclass
class LayoutReport:
    """What the fitter decided, for display in the interface."""

    body_size: float
    left: list[SectionKey]
    right: list[SectionKey]
    left_height: float
    right_height: float
    available: float
    words: int
    section_gap: float = 9.0

    @property
    def fill_ratio(self) -> float:
        """Tallest column as a share of available height."""
        return max(self.left_height, self.right_height) / self.available


@dataclass
class RenderResult:
    """The rendered PDF and its layout report."""

    pdf: bytes
    report: LayoutReport


def _text(value: str, fonts: Fonts) -> str:
    """Escape for Paragraph markup; strip characters the fallback font cannot draw."""
    value = " ".join(value.split())
    if not fonts.unicode:
        value = value.encode("cp1252", "replace").decode("cp1252")
    return escape(value)


class _Styles:
    def __init__(self, fonts: Fonts, size: float, primary: str) -> None:
        lead = size * LINE_HEIGHT
        self.body = ParagraphStyle("body", fontName=fonts.regular, fontSize=size, leading=lead,
                                   textColor=_INK, spaceAfter=size * 0.25)
        self.bullet = ParagraphStyle("bullet", parent=self.body, leftIndent=size * 1.05,
                                     bulletIndent=0, bulletFontName=fonts.bold,
                                     bulletColor=HexColor(primary), spaceAfter=size * 0.22)
        self.heading = ParagraphStyle("heading", fontName=fonts.bold, fontSize=size + 0.5,
                                      leading=(size + 0.5) * 1.25, textColor=HexColor(primary),
                                      spaceBefore=0, spaceAfter=size * 0.3)
        self.funding = ParagraphStyle("funding", parent=self.body)
        self.size = size


class _Rule(Flowable):
    """A thin coloured line under a section heading."""

    def __init__(self, width: float, color: str) -> None:
        super().__init__()
        self.width, self.color = width, color

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # noqa: N803
        return self.width, 3

    def draw(self) -> None:
        self.canv.setStrokeColor(HexColor(self.color))
        self.canv.setLineWidth(0.8)
        self.canv.line(0, 2, self.width, 2)


_LEAD_LABEL = re.compile(r"^(TAM|SAM|SOM)\b[:\s-]*", re.IGNORECASE)


def _item_markup(item: TeaserItem, fonts: Fonts) -> str:
    text = _text(item.text, fonts)
    if item.role in (ItemRole.TAM, ItemRole.SAM, ItemRole.SOM):
        label = item.role.value.upper()
        rest = _LEAD_LABEL.sub("", text)
        return f'<font name="{fonts.bold}">{label}</font>  {rest}'
    if item.role is ItemRole.TEAM_MEMBER:
        # Bold the name: everything before the first comma, dash, or "|".
        m = re.match(r"^(.{2,60}?)(\s*[,–—|-]\s+|\s*:\s+)(.*)$", text)
        if m:
            return f'<font name="{fonts.bold}">{m.group(1)}</font>{m.group(2)}{m.group(3)}'
    if item.role is ItemRole.FUNDING_ASK:
        return f'<font name="{fonts.bold}">{text}</font>'
    return text


def _section(draft: TeaserDraft, key: SectionKey, styles: _Styles, fonts: Fonts) -> Flowable | None:
    items = [i for i in draft.section_items(key, included_only=True) if i.text.strip()]
    if not items:
        return None
    heading = Paragraph(_text(draft.headings.get(key, key.value), fonts), styles.heading)
    parts: list[Flowable] = [heading, _Rule(COL_W, tint(draft.brand.accent, 0.45)), Spacer(1, 2)]
    single = len(items) == 1
    for item in items:
        markup = _item_markup(item, fonts)
        if single:
            parts.append(Paragraph(markup, styles.body))
        else:
            parts.append(Paragraph(markup, styles.bullet, bulletText="•" if fonts.unicode else "-"))
    if key is SectionKey.FUNDING:
        box = Table([[parts[0]], [parts[3:]]], colWidths=[COL_W])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), HexColor(tint(draft.brand.accent, 0.9))),
            ("LINEBEFORE", (0, 0), (0, -1), 2.5, HexColor(draft.brand.primary)),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
            ("TOPPADDING", (0, 1), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ]))
        return box
    # A single-cell table measures its true height in wrap() and draws as one unit, which
    # is what the column fitter needs. (KeepTogether reports a huge height to force splits.)
    block = Table([[parts]], colWidths=[COL_W])
    block.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return block


def _height(flow: Flowable) -> float:
    _, h = flow.wrap(COL_W, PAGE_H)
    return h


SECTION_GAP = 9.0


def _columns(heights: list[float]) -> tuple[int, float, float]:
    """Best split index (sections before it go left) and the two column heights."""
    best: tuple[int, float, float] | None = None
    n = len(heights)
    for k in range(1, n) if n > 1 else [1]:
        left = sum(heights[:k]) + SECTION_GAP * max(0, k - 1)
        right = sum(heights[k:]) + SECTION_GAP * max(0, n - k - 1)
        if best is None or max(left, right) < max(best[1], best[2]):
            best = (k, left, right)
    assert best is not None
    return best


LOGO_MAX_W = 110.0
LOGO_H = 34.0


def _logo(draft: TeaserDraft, options: RenderOptions) -> tuple[ImageReader, float, float] | None:
    if not (options.include_logo and draft.brand.logo_png):
        return None
    try:
        reader = ImageReader(io.BytesIO(draft.brand.logo_png))
        iw, ih = reader.getSize()
    except Exception:
        return None
    w = min(LOGO_MAX_W, LOGO_H * iw / ih)
    return reader, w, w * ih / iw


def _tagline(draft: TeaserDraft, fonts: Fonts, text_x: float) -> tuple[Paragraph, float] | None:
    if not (draft.tagline.include and draft.tagline.value.strip()):
        return None
    style = ParagraphStyle("tag", fontName=fonts.italic, fontSize=_TYPE.tagline_pt, leading=_TYPE.tagline_pt * 1.23, textColor=_MUTED)
    para = Paragraph(_text(draft.tagline.value, fonts), style)
    _, h = para.wrap(PAGE_W - MARGIN_X - text_x, 60)
    return para, h


def _text_x(draft: TeaserDraft, options: RenderOptions) -> float:
    logo = _logo(draft, options)
    return MARGIN_X + (logo[1] + 12 if logo else 0)


MAX_SECTION_GAP = 17.0


def _spread_gap(heights: list[float], k: int, available: float) -> float:
    """Section spacing: widened (up to a cap) to use spare height, never beyond what fits."""
    gap = MAX_SECTION_GAP
    for part in (heights[:k], heights[k:]):
        if len(part) > 1:
            gap = min(gap, (available - sum(part)) / (len(part) - 1))
    return max(SECTION_GAP, gap)


NAME_MAX_SIZE = _TYPE.company_name_pt.max
NAME_MIN_SIZE = _TYPE.company_name_pt.min
SITE_SIZE = _TYPE.website_pt
SITE_LINE = 14.0


@dataclass
class _HeaderPlan:
    """Header geometry, computed once and used both to measure and to draw."""

    x: float
    name: Paragraph
    name_h: float
    name_size: float
    website: str
    site_inline: bool
    tagline: tuple[Paragraph, float] | None
    logo: tuple[ImageReader, float, float] | None

    @property
    def height(self) -> float:
        """Vertical space consumed below the top margin, rule and spacing included."""
        text = self.name_h + (self.tagline[1] + 3 if self.tagline else 0)
        text += SITE_LINE if self.website and not self.site_inline else 0
        return max(text, (self.logo[2] - 4) if self.logo else 0) + 18


def _header_plan(draft: TeaserDraft, options: RenderOptions) -> _HeaderPlan:
    """Fit the company name beside the website; if it cannot, give the website its own line
    and let the name use the full width, wrapping if it is still too long."""
    fonts = register_fonts()
    x = _text_x(draft, options)
    name = draft.company_name.value.strip()
    website = draft.website.value.strip() if draft.website.include else ""
    full_w = PAGE_W - MARGIN_X - x
    inline_w = full_w - (pdfmetrics.stringWidth(website, fonts.regular, SITE_SIZE) + 16 if website else 0)

    def fit(width: float) -> float:
        size = NAME_MAX_SIZE
        while size > NAME_MIN_SIZE and pdfmetrics.stringWidth(name, fonts.bold, size) > width:
            size -= 0.5
        return size

    size = fit(inline_w)
    site_inline = not website or pdfmetrics.stringWidth(name, fonts.bold, size) <= inline_w
    width = inline_w if site_inline else full_w
    if not site_inline:
        size = fit(full_w)
    style = ParagraphStyle("name", fontName=fonts.bold, fontSize=size, leading=size * 1.15,
                           textColor=HexColor(draft.brand.primary))
    para = Paragraph(_text(name, fonts), style)
    _, name_h = para.wrap(width, 200)
    return _HeaderPlan(x, para, name_h, size, website, site_inline,
                       _tagline(draft, fonts, x), _logo(draft, options))


def _header_height(draft: TeaserDraft, options: RenderOptions) -> float:
    """Exactly the vertical space ``_draw_header`` consumes below the top margin."""
    return _header_plan(draft, options).height


def _footer_height(options: RenderOptions) -> float:
    return 34 + (20 if options.ten_footer else 0)


def plan_layout(draft: TeaserDraft, options: RenderOptions | None = None) -> tuple[LayoutReport, list[Flowable], list[Flowable]]:
    """Choose a body size and column split, or raise LayoutOverflowError."""
    options = options or RenderOptions()
    fonts = register_fonts()
    available = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM - _header_height(draft, options) - _footer_height(options)
    from teaser.editing import word_count

    words = word_count(draft)
    worst = 0.0
    for size in BODY_SIZES:
        styles = _Styles(fonts, size, draft.brand.primary)
        keys: list[SectionKey] = []
        flows: list[Flowable] = []
        for key in ORDER:
            flow = _section(draft, key, styles, fonts)
            if flow is not None:
                keys.append(key)
                flows.append(flow)
        if not flows:
            raise RenderError("There is no included content to render.")
        heights = [_height(f) for f in flows]
        k, left_h, right_h = _columns(heights)
        tallest = max(left_h, right_h)
        if tallest <= available:
            gap = _spread_gap(heights, k, available)
            left_h = sum(heights[:k]) + gap * max(0, k - 1)
            right_h = sum(heights[k:]) + gap * max(0, len(heights) - k - 1)
            report = LayoutReport(size, keys[:k], keys[k:], left_h, right_h, available, words, gap)
            return report, flows[:k], flows[k:]
        worst = tallest - available
    lines = int(worst // (MIN_BODY_SIZE * LINE_HEIGHT)) + 1
    raise LayoutOverflowError(
        f"The content does not fit on one page even at the minimum {MIN_BODY_SIZE:g} pt body "
        f"size: about {lines} line(s) too long ({words} words). Shorten or exclude some items "
        f"(roughly {max(10, lines * 7)} words) and try again.",
        overflow_lines=lines,
        words=words,
    )


def _draw_header(c: Canvas, draft: TeaserDraft, fonts: Fonts, options: RenderOptions) -> float:
    primary = HexColor(draft.brand.primary)
    c.setFillColor(primary)
    c.rect(0, PAGE_H - 8, PAGE_W, 8, stroke=0, fill=1)
    top = PAGE_H - MARGIN_TOP
    plan = _header_plan(draft, options)
    if plan.logo:
        reader, w, h = plan.logo
        c.drawImage(reader, MARGIN_X, top - h + 4, width=w, height=h, mask="auto")
    y = top + 4
    plan.name.drawOn(c, plan.x, y - plan.name_h)
    c.setFillColor(_MUTED)
    c.setFont(fonts.regular, SITE_SIZE)
    if plan.website and plan.site_inline:
        # Baseline aligned with the first line of the name.
        c.drawRightString(PAGE_W - MARGIN_X, y - plan.name_size * 0.95, plan.website)
    y -= plan.name_h + 4
    if plan.tagline:
        para, h = plan.tagline
        para.drawOn(c, plan.x, y - 3 - h)
        y -= h + 3
    if plan.website and not plan.site_inline:
        c.setFillColor(_MUTED)
        c.setFont(fonts.regular, SITE_SIZE)
        c.drawString(plan.x, y - SITE_LINE + 4, plan.website)
        y -= SITE_LINE
    if plan.logo:
        y = min(y, top - plan.logo[2] + 4)
    y -= 8
    c.setStrokeColor(primary)
    c.setLineWidth(1.4)
    c.line(MARGIN_X, y, PAGE_W - MARGIN_X, y)
    return y - 10


def _draw_footer(c: Canvas, draft: TeaserDraft, fonts: Fonts, options: RenderOptions) -> None:
    y = MARGIN_BOTTOM + (20 if options.ten_footer else 0)
    c.setStrokeColor(HexColor("#C9CDD2"))
    c.setLineWidth(0.5)
    c.line(MARGIN_X, y + 26, PAGE_W - MARGIN_X, y + 26)
    style = ParagraphStyle("disc", fontName=fonts.italic, fontSize=DISCLAIMER_SIZE,
                           leading=DISCLAIMER_SIZE * 1.3, textColor=_MUTED, alignment=1)
    para = Paragraph(_text(draft.disclaimer, fonts), style)
    _, h = para.wrap(PAGE_W - 2 * MARGIN_X, 30)
    para.drawOn(c, MARGIN_X, y + 20 - h)
    if options.ten_footer:
        _draw_ten_footer(c, draft, options)


def _draw_ten_footer(c: Canvas, draft: TeaserDraft, options: RenderOptions) -> None:
    """TEN Capital standard footer: title, page number, compiled-on line, logo. Open Sans 7 pt."""
    fonts = register_fonts()
    date = f"{options.compiled_on:%B} {options.compiled_on.day}, {options.compiled_on.year}"
    logo_w, logo_h = 0.67 * inch, 0.25 * inch
    text = ""
    for title in (f"{draft.company_name.value.strip()} Investor Teaser", "Investor Teaser"):
        text = f"{title}          1          Compiled on {date} by TEN Capital Network    "
        if c.stringWidth(text, fonts.regular, 7) + logo_w <= PAGE_W - 2 * MARGIN_X:
            break  # a very long company name falls back to the generic title
    width = c.stringWidth(text, fonts.regular, 7) + logo_w
    x = (PAGE_W - width) / 2
    y = MARGIN_BOTTOM - 2
    c.setFillColor(_MUTED)
    c.setFont(fonts.regular, 7)
    c.drawString(x, y + 7, text)
    if TEN_LOGO.is_file():
        c.drawImage(str(TEN_LOGO), x + width - logo_w, y, width=logo_w, height=logo_h, mask="auto")


def render_pdf(draft: TeaserDraft, options: RenderOptions | None = None) -> RenderResult:
    """Render the teaser, or raise LayoutOverflowError / RenderError."""
    options = options or RenderOptions()
    fonts = register_fonts()
    report, left, right = plan_layout(draft, options)

    buffer = io.BytesIO()
    c = Canvas(buffer, pagesize=letter)
    name = draft.company_name.value.strip() or "Company"
    c.setTitle(f"{name} Investor Teaser")
    c.setAuthor(name)
    c.setSubject("Investor teaser")
    top = _draw_header(c, draft, fonts, options)
    bottom = MARGIN_BOTTOM + _footer_height(options)
    height = top - bottom
    if height < max(report.left_height, report.right_height) - 0.5:
        raise RenderError("Internal layout mismatch: header taller than planned.")

    for x, flows in ((MARGIN_X, left), (MARGIN_X + COL_W + GUTTER, right)):
        frame = Frame(x, bottom, COL_W, height, leftPadding=0, rightPadding=0, topPadding=0,
                      bottomPadding=0, showBoundary=0)
        story: list[Flowable] = []
        for i, flow in enumerate(flows):
            if i:
                story.append(Spacer(1, report.section_gap))
            story.append(flow)
        frame.addFromList(story, c)
        if story:  # anything left over did not fit: never silently drop it
            raise RenderError("Content did not fit its column; shorten the text and retry.")

    _draw_footer(c, draft, fonts, options)
    c.showPage()
    c.save()
    pdf = buffer.getvalue()
    pages = page_count(pdf)
    if pages != 1:
        raise RenderError(f"Rendered PDF has {pages} pages; expected exactly one.")
    return RenderResult(pdf=pdf, report=report)


def page_count(pdf: bytes) -> int:
    """Number of pages in a PDF."""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


def preview_png(pdf: bytes, dpi: int = 110) -> bytes:
    """Rasterize page 1 for on-screen preview."""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_pixmap(dpi=dpi, alpha=False).tobytes("png")


def layout_audit(pdf: bytes) -> list[str]:
    """Geometric checks on the rendered page: text inside the page, no overlapping text.

    Spans (runs of same-styled text) are compared pairwise, so two strings drawn on the
    same baseline over each other are caught even when PyMuPDF merges them into one block.
    Each span's box is shrunk vertically to its glyph body, so adjacent lines whose
    ascenders and descenders touch are not reported.
    """
    issues: list[str] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        rect = page.rect
        spans = [
            (pymupdf.Rect(s["bbox"]), s["text"].strip(), s["size"])
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for s in line["spans"]
            if s["text"].strip()
        ]
    for box, text, _ in spans:
        if box.x0 < rect.x0 + 18 or box.x1 > rect.x1 - 18 or box.y0 < rect.y0 + 6 or box.y1 > rect.y1 - 6:
            issues.append(f"Text outside safe margins: {text[:40]!r}")
    bodies = []
    for box, text, size in spans:
        pad = size * 0.22
        bodies.append((pymupdf.Rect(box.x0, box.y0 + pad, box.x1, box.y1 - pad), text))
    for i, (a, at) in enumerate(bodies):
        for b, bt in bodies[i + 1 :]:
            ix = min(a.x1, b.x1) - max(a.x0, b.x0)
            iy = min(a.y1, b.y1) - max(a.y0, b.y0)
            if ix > 1.0 and iy > 1.0:
                issues.append(f"Overlapping text: {at[:30]!r} / {bt[:30]!r}")
    return issues
