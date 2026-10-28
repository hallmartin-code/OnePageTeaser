"""Missing information: shown in review, omitted from the PDF, never invented."""

from __future__ import annotations

import pymupdf

from teaser.editing import export_blockers, missing_sections
from teaser.extract.offline import OfflineExtractor
from teaser.extract.pipeline import build_draft
from teaser.render import render_pdf
from teaser.schema import DEFAULT_HEADINGS, LLMDraft, LLMField, LLMItem, SectionKey

from .conftest import approve_all, make_deck

SPARSE = make_deck(
    "Nimbus Analytics",
    "The Problem\nRetailers waste 30% of promotional spend on the wrong stores.",
    "Our Solution\nNimbus predicts store-level promotion lift using point-of-sale data.",
)


def _sparse_draft():  # type: ignore[no-untyped-def]
    llm = LLMDraft(
        company_name=LLMField(value="Nimbus Analytics", pages=[1], quote="Nimbus Analytics"),
        items=[
            LLMItem(section=SectionKey.PROBLEM_MARKET, text="Retailers waste 30% of promotional spend on the wrong stores.",
                    pages=[2], quotes=["Retailers waste 30% of promotional spend on the wrong stores."]),
            LLMItem(section=SectionKey.SOLUTION_TECHNOLOGY, text="Predicts store-level promotion lift from point-of-sale data.",
                    pages=[3], quotes=["Nimbus predicts store-level promotion lift using point-of-sale data."]),
        ],
    )
    return build_draft(llm, SPARSE, generated_by="test")


def test_missing_sections_and_header_fields_are_reported() -> None:
    draft = _sparse_draft()
    assert draft.tagline.value == "" and draft.website.value == ""
    assert set(missing_sections(draft)) == {
        SectionKey.PIPELINE_PRODUCT, SectionKey.MARKET_SIZE, SectionKey.TRACTION, SectionKey.TEAM, SectionKey.FUNDING
    }


def test_empty_sections_are_omitted_from_the_pdf() -> None:
    draft = _sparse_draft()
    approve_all(draft)
    assert export_blockers(draft) == []
    with pymupdf.open(stream=render_pdf(draft).pdf, filetype="pdf") as doc:
        text = doc[0].get_text()
    assert DEFAULT_HEADINGS[SectionKey.PROBLEM_MARKET] in text
    for key in (SectionKey.TEAM, SectionKey.FUNDING, SectionKey.MARKET_SIZE, SectionKey.TRACTION):
        assert DEFAULT_HEADINGS[key] not in text
    assert "None" not in text and "null" not in text


def test_missing_company_name_blocks_export() -> None:
    draft = _sparse_draft()
    draft.company_name.value = ""
    assert "Company name is missing." in export_blockers(draft)


def test_offline_extractor_does_not_invent_header_fields() -> None:
    llm = OfflineExtractor().extract(SPARSE)
    assert llm.company_name.value == "Nimbus Analytics"
    assert llm.website.value is None
    assert llm.tagline.value is None
    assert all(item.quotes and item.quotes[0] in SPARSE.page_text(item.pages[0]) for item in llm.items)


def test_excluded_header_fields_are_omitted() -> None:
    deck = make_deck("Nimbus Analytics\nPromotion intelligence for retail\nwww.nimbus.example",
                     "The Problem\nRetailers waste 30% of promotional spend.")
    draft = build_draft(OfflineExtractor().extract(deck), deck, generated_by="offline")
    assert draft.website.value == "www.nimbus.example"
    draft.website.include = False
    approve_all(draft)
    with pymupdf.open(stream=render_pdf(draft).pdf, filetype="pdf") as doc:
        assert "nimbus.example" not in doc[0].get_text()
