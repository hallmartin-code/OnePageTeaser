"""The document structure template: valid, company-neutral, and actually used by the app."""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from teaser.extract.prompts import SYSTEM_PROMPT
from teaser.render import pdf as pdf_mod
from teaser.render import render_pdf
from teaser.schema import DEFAULT_HEADINGS, DISCLAIMER, SECTION_PURPOSE, SectionKey
from teaser.template import TEMPLATE_PATH, Template, load_template

from .conftest import ROOT, approve_all

TEMPLATE = load_template()
MARKDOWN = (ROOT / "templates" / "teaser_template.md").read_text(encoding="utf-8")

#: Anything specific to the synthetic sample company must never leak into the template.
SAMPLE_SPECIFIC = ["Halcyon", "HT-101", "IBD", "Okafor", "Reyes", "Natarajan", "Ortiz", "Northwind",
                   "Bluefin", "22.4", "6.1B", "610M", "$15M", "colitis", "halcyon-tx"]


def test_template_is_company_neutral() -> None:
    for text in (TEMPLATE_PATH.read_text(encoding="utf-8"), MARKDOWN):
        for token in SAMPLE_SPECIFIC:
            assert token not in text, token
        # No monetary amounts: the template defines fields, not values.
        assert not re.search(r"[$€£]\s?\d", text)


def test_sections_match_code_in_order() -> None:
    assert TEMPLATE.section_keys == [k.value for k in SectionKey]
    for spec in TEMPLATE.sections:
        key = SectionKey(spec.key)
        assert DEFAULT_HEADINGS[key] == spec.default_heading
        assert SECTION_PURPOSE[key] == spec.purpose
        assert spec.content, f"{spec.key} lists no fields to analyse"


def test_markdown_spec_matches_json() -> None:
    for spec in TEMPLATE.sections:
        assert f"`{spec.key}`" in MARKDOWN
        assert spec.default_heading in MARKDOWN
    assert TEMPLATE.footer.disclaimer in MARKDOWN
    for industry in TEMPLATE.industry_heading_variants:
        assert industry in MARKDOWN


def test_code_uses_template_values() -> None:
    assert DISCLAIMER == TEMPLATE.footer.disclaimer
    assert pdf_mod.BODY_SIZES == (10.5, 10.0, 9.5, 9.0)
    assert pdf_mod.MIN_BODY_SIZE == TEMPLATE.typography.body_pt.min
    assert pdf_mod.DISCLAIMER_SIZE == TEMPLATE.typography.disclaimer_pt
    assert pdf_mod.ORDER == list(SectionKey)
    for spec in TEMPLATE.sections:
        assert spec.purpose in SYSTEM_PROMPT  # the model is asked for the template's fields
        for field in spec.content:
            assert field in SYSTEM_PROMPT


def test_rendered_sections_follow_template_order(sample_draft) -> None:  # type: ignore[no-untyped-def]
    approve_all(sample_draft)
    report = render_pdf(sample_draft).report
    rendered = report.left + report.right
    assert rendered == [k for k in pdf_mod.ORDER if k in rendered]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("typography", "body_pt", "min"), 8.0, "9 pt"),
        (("industry_heading_variants", "biotech", "nonexistent"), "X", "unknown sections"),
        (("page", "max_pages"), 2, "less than or equal to 1"),
    ],
)
def test_invalid_templates_are_rejected(path: tuple[str, ...], value: object, message: str) -> None:
    data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError, match=message):
        Template.model_validate(data)
