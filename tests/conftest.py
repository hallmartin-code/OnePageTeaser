from __future__ import annotations

import json
from pathlib import Path

import pytest

from teaser.extract.pipeline import build_draft
from teaser.ingest import load_deck
from teaser.ingest.models import Deck, Page
from teaser.schema import LLMDraft, Severity, TeaserDraft

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


@pytest.fixture(scope="session")
def sample_pdf_deck() -> Deck:
    return load_deck((SAMPLES / "sample_deck.pdf").read_bytes(), "sample_deck.pdf", enable_ocr=False)


@pytest.fixture(scope="session")
def sample_pptx_deck() -> Deck:
    return load_deck((SAMPLES / "sample_deck.pptx").read_bytes(), "sample_deck.pptx", enable_ocr=False)


@pytest.fixture
def sample_llm() -> LLMDraft:
    return LLMDraft.model_validate(json.loads((SAMPLES / "sample_model_output.json").read_text(encoding="utf-8")))


@pytest.fixture
def sample_draft(sample_pdf_deck: Deck, sample_llm: LLMDraft) -> TeaserDraft:
    return build_draft(sample_llm, sample_pdf_deck, generated_by="test")


def make_deck(*pages: str) -> Deck:
    """A synthetic deck from page texts."""
    return Deck(
        filename="synthetic.pdf",
        source_format="pdf",
        pages=[Page(index=i, title=t.split("\n")[0], text=t) for i, t in enumerate(pages, start=1)],
    )


def approve_all(draft: TeaserDraft) -> None:
    for flag in draft.flags:
        if flag.severity is Severity.BLOCKING:
            flag.approved = True
