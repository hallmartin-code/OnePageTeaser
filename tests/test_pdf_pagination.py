"""PDF output: exactly one page, minimum font size, no clipping or overlap, refuse overflow."""

from __future__ import annotations

import io

import pymupdf
import pytest
from docx import Document

from teaser.errors import LayoutOverflowError
from teaser.render import RenderOptions, layout_audit, page_count, render_pdf
from teaser.render.docx_export import render_docx
from teaser.render.pdf import MIN_BODY_SIZE, plan_layout
from teaser.schema import DISCLAIMER, HeaderField, SectionKey, TeaserDraft, TeaserItem

from .conftest import approve_all

SENTENCE = ("The platform reduces fulfilment cost per order by automating the slowest manual steps "
            "in mid-sized warehouses across North America and Europe.")


def _draft(n_items_per_section: int, sentence: str = SENTENCE) -> TeaserDraft:
    items = [TeaserItem(section=key, text=f"{sentence} ({key.value} {i})", user_added=True)
             for key in SectionKey for i in range(n_items_per_section)]
    return TeaserDraft(company_name=HeaderField(value="Acme Robotics"),
                       tagline=HeaderField(value="Warehouse picking robots for mid-sized fulfilment centres"),
                       website=HeaderField(value="www.acme-robotics.example"), items=items)


def _sizes(pdf: bytes) -> list[float]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        spans = [s for b in doc[0].get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]]
    return [round(s["size"], 1) for s in spans if s["text"].strip()]


def test_sample_renders_one_clean_page(sample_draft) -> None:  # type: ignore[no-untyped-def]
    approve_all(sample_draft)
    result = render_pdf(sample_draft)
    assert page_count(result.pdf) == 1
    assert layout_audit(result.pdf) == []
    assert result.report.body_size >= MIN_BODY_SIZE
    with pymupdf.open(stream=result.pdf, filetype="pdf") as doc:
        page = doc[0]
        assert (round(page.rect.width), round(page.rect.height)) == (612, 792)  # US Letter portrait
        text = page.get_text()
    assert DISCLAIMER in " ".join(text.split())
    assert "TEN Capital Network" in text


def test_no_text_below_minimum_size_except_footer(sample_draft) -> None:  # type: ignore[no-untyped-def]
    approve_all(sample_draft)
    sizes = _sizes(render_pdf(sample_draft, RenderOptions(ten_footer=False)).pdf)
    assert min(sizes) >= MIN_BODY_SIZE  # 9 pt disclaimer is the smallest text


def test_dense_content_shrinks_but_never_below_minimum() -> None:
    # Grow the content until it no longer fits at the largest size, but still fits at all.
    draft = None
    for extra in range(0, 60, 3):
        candidate = _draft(2, sentence=SENTENCE + " Additional detail." * (extra // 3))
        try:
            report, *_ = plan_layout(candidate)
        except LayoutOverflowError:
            break
        if report.body_size < 10.5:
            draft = candidate
            break
    assert draft is not None, "no content size exercised the shrink path"
    assert MIN_BODY_SIZE <= report.body_size < 10.5
    result = render_pdf(draft)
    assert page_count(result.pdf) == 1 and layout_audit(result.pdf) == []


def test_overflow_is_refused_not_clipped() -> None:
    draft = _draft(5)
    with pytest.raises(LayoutOverflowError) as info:
        render_pdf(draft)
    assert info.value.overflow_lines > 0
    assert "Shorten" in str(info.value)


def test_every_included_word_is_on_the_page() -> None:
    draft = _draft(1)
    with pymupdf.open(stream=render_pdf(draft).pdf, filetype="pdf") as doc:
        text = " ".join(doc[0].get_text().split())
    for item in draft.items:
        assert f"({item.section.value} 0)" in text  # last words of every item: nothing truncated


def test_long_header_and_unicode_render_without_overlap() -> None:
    draft = _draft(1, sentence="Revenue grew 3× to €1.2M — with ≥95% retention and “net” expansion.")
    draft.company_name.value = "Extraordinarily Long Company Name Holdings International"
    draft.tagline.value = "A tagline long enough to wrap onto a second line in the header of the teaser page"
    result = render_pdf(draft)
    assert page_count(result.pdf) == 1
    assert layout_audit(result.pdf) == []
    with pymupdf.open(stream=result.pdf, filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "€1.2M" in text and "≥95%" in text


def test_audit_detects_text_drawn_over_text() -> None:
    from reportlab.pdfgen.canvas import Canvas

    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(612, 792))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(60, 700, "Extraordinarily Long Company Name")
    c.setFont("Helvetica", 10)
    c.drawRightString(400, 702, "www.example.com")  # same line, drawn over the name
    c.drawString(60, 600, "Clean text elsewhere")
    c.save()
    assert any("Overlapping" in i for i in layout_audit(buf.getvalue()))


def test_long_name_moves_website_to_its_own_line() -> None:
    draft = _draft(1)
    draft.company_name.value = "Extraordinarily Long Company Name Holdings International Incorporated"
    draft.website.value = "www.a-rather-long-company-website.example"
    result = render_pdf(draft)
    assert layout_audit(result.pdf) == []
    with pymupdf.open(stream=result.pdf, filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "a-rather-long-company-website" in text and "Incorporated" in text


def test_logo_option_renders_one_page(sample_draft) -> None:  # type: ignore[no-untyped-def]
    from teaser.config import ASSETS

    approve_all(sample_draft)
    sample_draft.brand.logo_png = (ASSETS / "TEN_Capital_logo_footer.png").read_bytes()
    result = render_pdf(sample_draft, RenderOptions(include_logo=True))
    assert page_count(result.pdf) == 1 and layout_audit(result.pdf) == []


def test_docx_export_contains_content_and_disclaimer(sample_draft) -> None:  # type: ignore[no-untyped-def]
    approve_all(sample_draft)
    doc = Document(io.BytesIO(render_docx(sample_draft)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Halcyon Therapeutics" in text and DISCLAIMER in text
    assert "Northwind" not in text
