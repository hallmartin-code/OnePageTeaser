"""Source fidelity: figures, units, qualifiers, quotes, and page references."""

from __future__ import annotations

import pytest

from teaser.editing import export_blockers, recheck_item
from teaser.extract.pipeline import build_draft
from teaser.fidelity import figures, qualifier_drift, unmatched_figures
from teaser.schema import FlagKind, LLMDraft, LLMField, LLMItem, SectionKey, Severity

from .conftest import approve_all, make_deck

DECK = make_deck(
    "Acme Robotics\nWarehouse picking robots\nwww.acme-robotics.example",
    "Market\nTAM: $4.2B global warehouse automation (2024)\nSOM: $120M projected by 2028",
    "Traction\nRevenue of €1.5 million in FY2024\nLOI with three regional retailers\n40% gross margin",
    "Regulatory\nWe plan to submit for CE marking in 2026",
)


def _draft(*items: LLMItem, **header: LLMField) -> object:
    return build_draft(LLMDraft(items=list(items), company_name=LLMField(value="Acme Robotics", pages=[1], quote="Acme Robotics"), **header), DECK, generated_by="test")


def _kinds(draft, item) -> set[FlagKind]:  # type: ignore[no-untyped-def]
    return {f.kind for f in draft.flags_for(item.id)}


@pytest.mark.parametrize(
    ("claim", "source", "ok"),
    [
        ("TAM of $4.2B", "TAM: $4.2B global", True),
        ("TAM of $4.2 billion", "TAM: $4.2B global", True),
        ("TAM of $4.2M", "TAM: $4.2B global", False),  # magnitude changed
        ("TAM of €4.2B", "TAM: $4.2B global", False),  # currency changed
        ("40 percent gross margin", "40% gross margin", True),
        ("three retailers", "LOI with 3 regional retailers", True),
        ("revenue of €1.5M", "Revenue of €1.5 million", True),
        ("revenue of €15M", "Revenue of €1.5 million", False),
    ],
)
def test_figures_preserve_units_magnitudes_and_currencies(claim: str, source: str, ok: bool) -> None:
    assert (unmatched_figures(claim, [source]) == []) is ok


def test_figure_parsing() -> None:
    parsed = figures("$22.4B TAM, 62% reduction, n=24")
    assert [(f.value, f.kind, f.currency) for f in parsed] == [
        (22.4e9, "num", "USD"), (62.0, "pct", None), (24.0, "num", None)
    ]


def test_faithful_item_has_no_accuracy_flags() -> None:
    item = LLMItem(section=SectionKey.MARKET_SIZE, role="tam", text="TAM: $4.2B global warehouse automation market (2024).",
                   pages=[2], quotes=["TAM: $4.2B global warehouse automation (2024)"])
    draft = _draft(item)
    assert _kinds(draft, draft.items[0]) == set()
    assert draft.items[0].refs[0].verified


def test_fabricated_figure_is_flagged_and_blocks_export() -> None:
    item = LLMItem(section=SectionKey.TRACTION, text="Revenue of €2.5 million in FY2024.",
                   pages=[3], quotes=["Revenue of €1.5 million in FY2024"])
    draft = _draft(item)
    flags = draft.flags_for(draft.items[0].id)
    mismatch = [f for f in flags if f.kind is FlagKind.FIGURE_MISMATCH]
    assert mismatch and mismatch[0].severity is Severity.BLOCKING
    assert "€2.5 million" in mismatch[0].message
    assert any("not found" in b for b in export_blockers(draft))


def test_quote_not_on_cited_page_is_unsupported() -> None:
    item = LLMItem(section=SectionKey.TRACTION, text="Strong customer demand.", pages=[2],
                   quotes=["Customers love the product"])
    draft = _draft(item)
    assert FlagKind.UNSUPPORTED in _kinds(draft, draft.items[0])


def test_quote_on_a_different_page_is_reattributed() -> None:
    # Model cites pages [2, 3] with quotes in the "wrong" order; each quote finds its page.
    item = LLMItem(section=SectionKey.TRACTION, text="40% gross margin and a $4.2B TAM.", pages=[2, 3],
                   quotes=["40% gross margin", "TAM: $4.2B"])
    draft = _draft(item)
    refs = {(r.page, r.quote) for r in draft.items[0].refs}
    assert refs == {(3, "40% gross margin"), (2, "TAM: $4.2B")}
    assert _kinds(draft, draft.items[0]) == set()


def test_reference_to_nonexistent_page_is_flagged() -> None:
    item = LLMItem(section=SectionKey.TRACTION, text="40% gross margin.", pages=[9], quotes=["40% gross margin"])
    draft = _draft(item)
    assert any("does not have" in f.message for f in draft.flags_for(draft.items[0].id))


def test_item_without_reference_is_blocking() -> None:
    draft = _draft(LLMItem(section=SectionKey.TRACTION, text="Market leader in Europe."))
    flags = draft.flags_for(draft.items[0].id)
    assert any(f.kind is FlagKind.UNSUPPORTED and f.severity is Severity.BLOCKING for f in flags)


@pytest.mark.parametrize(
    ("claim", "source", "phrase"),
    [
        ("SOM of $120M.", "SOM: $120M projected by 2028", "forward-looking"),
        ("Signed agreements with regional retailers.", "LOI with three regional retailers", "non-binding"),
        ("CE mark approved.", "We plan to submit for CE marking in 2026", "planned regulatory"),
    ],
)
def test_qualifier_drift_is_detected(claim: str, source: str, phrase: str) -> None:
    issues = qualifier_drift(claim, source)
    assert issues and phrase in " ".join(issues)


@pytest.mark.parametrize(
    ("claim", "source"),
    [
        ("SOM of $120M projected by 2028.", "SOM: $120M projected by 2028"),
        ("LOIs with three regional retailers.", "LOI with three regional retailers"),
        ("Plans to submit for CE marking in 2026.", "We plan to submit for CE marking in 2026"),
    ],
)
def test_preserved_qualifiers_pass(claim: str, source: str) -> None:
    assert qualifier_drift(claim, source) == []


def test_invented_header_values_are_dropped() -> None:
    draft = _draft(
        tagline=LLMField(value="The future of logistics", pages=[1], quote="The future of logistics"),
        website=LLMField(value="www.acme.com", pages=[1], quote="www.acme.com"),
    )
    assert draft.tagline.value == ""
    assert draft.website.value == ""
    assert draft.company_name.value == "Acme Robotics"


def test_edit_rechecks_figures_and_keeps_unchanged_approvals() -> None:
    item = LLMItem(section=SectionKey.TRACTION, text="Revenue of €1.5 million in FY2024.", pages=[3],
                   quotes=["Revenue of €1.5 million in FY2024"])
    draft = _draft(item)
    target = draft.items[0]
    assert _kinds(draft, target) == set()

    target.text = "Revenue of €9 million in FY2024."
    recheck_item(draft, target, DECK)
    mismatch = [f for f in draft.flags_for(target.id) if f.kind is FlagKind.FIGURE_MISMATCH]
    assert mismatch
    mismatch[0].approved = True
    recheck_item(draft, target, DECK)  # same text -> same flag -> approval kept
    assert all(f.approved for f in draft.flags_for(target.id) if f.kind is FlagKind.FIGURE_MISMATCH)

    target.text = "Revenue of €1.5 million in FY2024."
    recheck_item(draft, target, DECK)
    assert FlagKind.FIGURE_MISMATCH not in _kinds(draft, target)


def test_sample_fixture_is_fully_grounded(sample_draft) -> None:  # type: ignore[no-untyped-def]
    accuracy = [f for f in sample_draft.flags
                if f.kind in (FlagKind.UNSUPPORTED, FlagKind.FIGURE_MISMATCH, FlagKind.QUALIFIER_DRIFT)]
    assert accuracy == []
    approve_all(sample_draft)
    assert export_blockers(sample_draft) == []
