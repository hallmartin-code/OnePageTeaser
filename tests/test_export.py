"""Export: every format, the review record, the draft watermark, and gating."""

from __future__ import annotations

import io
import json
import zipfile

import pymupdf
import pytest
from docx import Document
from PIL import Image

from teaser import export
from teaser.render import RenderOptions, page_count

from .conftest import approve_all

SENSITIVE = ["Northwind", "Bluefin", "6.5M", "38M", "maya.okafor", "555-0142", "63/482,117",
             "CC(C)Cc1", "Midwest GI", "top-10 pharma"]
SOURCE = export.SourceInfo("sample_deck.pdf", 12, [], "test")


def _text(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return " ".join(doc[0].get_text().split())


def test_export_refused_while_approvals_open(sample_draft) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="approval"):
        export.build_bundle(sample_draft, SOURCE, RenderOptions())


def test_bundle_contains_every_format(sample_draft) -> None:  # type: ignore[no-untyped-def]
    approve_all(sample_draft)
    bundle = export.build_bundle(sample_draft, SOURCE, RenderOptions())
    assert bundle.stem == "Halcyon_Therapeutics_Investor_Teaser"
    assert page_count(bundle.pdf) == 1
    assert "Halcyon Therapeutics" in "\n".join(p.text for p in Document(io.BytesIO(bundle.docx)).paragraphs)
    width, height = Image.open(io.BytesIO(bundle.png)).size
    assert height / width == pytest.approx(11 / 8.5, rel=0.01)  # US Letter portrait
    with zipfile.ZipFile(io.BytesIO(bundle.zip())) as archive:
        assert sorted(archive.namelist()) == sorted(bundle.files())
        assert archive.read(f"{bundle.stem}.pdf") == bundle.pdf


def test_review_record_is_an_audit_trail_without_excluded_text(sample_draft) -> None:  # type: ignore[no-untyped-def]
    approve_all(sample_draft)
    bundle = export.build_bundle(sample_draft, SOURCE, RenderOptions())
    raw = bundle.record.decode("utf-8")
    record = json.loads(raw)
    assert record["company"]["name"] == "Halcyon Therapeutics"
    assert record["template"]["id"] == "one_page_investor_teaser"
    assert record["layout"]["pages"] == 1
    tam = next(i for s in record["sections"] if s["key"] == "market_size" for i in s["items"] if i["role"] == "tam")
    assert tam["sources"] == [{"page": 5, "quote": "TAM: $22.4B global IBD therapeutics market (2025).", "verified": True}]
    assert any(a["category"] == "funding_ask" for a in record["approvals"])
    assert record["excluded_items"] and all("text" not in e for e in record["excluded_items"])
    for s in SENSITIVE:
        assert s not in raw


def test_draft_pdf_is_watermarked_one_page(sample_draft) -> None:  # type: ignore[no-untyped-def]
    pdf = export.draft_pdf(sample_draft, RenderOptions())  # allowed before approval
    assert page_count(pdf) == 1
    text = _text(pdf)
    assert export.DRAFT_WATERMARK in text and "Halcyon Therapeutics" in text
    for s in SENSITIVE:
        assert s not in text  # excluded items stay out of drafts too


def test_file_stem_is_filesystem_safe(sample_draft) -> None:  # type: ignore[no-untyped-def]
    sample_draft.company_name.value = "Acme / Bio: Labs, Inc."
    assert export.file_stem(sample_draft) == "Acme_Bio_Labs_Inc_Investor_Teaser"
    assert export.file_stem(sample_draft, draft_copy=True).endswith("_DRAFT")
