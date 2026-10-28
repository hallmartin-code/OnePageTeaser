"""Deck -> LLMDraft -> verified, screened TeaserDraft."""

from __future__ import annotations

from teaser.brand import detect_brand
from teaser.editing import apply_review
from teaser.extract.providers import Provider
from teaser.fidelity import quote_on_page
from teaser.ingest.models import Deck
from teaser.schema import (
    DEFAULT_HEADINGS,
    HeaderField,
    LLMDraft,
    LLMField,
    LLMItem,
    SourceRef,
    TeaserDraft,
    TeaserItem,
)
from teaser.screening import screen_deck


def _refs(pages: list[int], quotes: list[str], deck: Deck) -> list[SourceRef]:
    """Pair each quote with the cited page it actually appears on.

    Models do not always list pages and quotes in matching order, so each quote is looked
    up across the cited pages rather than zipped positionally. Pages with no quote of their
    own are kept as unquoted references; pages outside the deck are kept so the review
    flags them rather than silently dropping them.
    """
    cited = list(dict.fromkeys(pages))
    refs: list[SourceRef] = []
    used: set[int] = set()
    for quote in (q.strip() for q in quotes):
        if not quote:
            continue
        page = next((p for p in cited if quote_on_page(quote, deck.page_text(p))), None)
        if page is None:
            refs.append(SourceRef(page=cited[0] if cited else 1, quote=quote, verified=False))
            used.add(cited[0] if cited else 1)
        else:
            refs.append(SourceRef(page=page, quote=quote, verified=True))
            used.add(page)
    refs.extend(SourceRef(page=p, quote=None) for p in cited if p not in used and p >= 1)
    return refs


def _header(field: LLMField, deck: Deck) -> HeaderField:
    if not field.value or not field.value.strip():
        return HeaderField()
    value = field.value.strip()
    quotes = [field.quote or value]
    refs = _refs(field.pages or [1], quotes, deck)
    if not any(r.verified for r in refs):
        # A header value the deck does not contain is exactly the kind of invention the
        # teaser must not carry (a guessed website or tagline). Drop it; the UI will show
        # the field as missing.
        whole = any(quote_on_page(value, p.text) for p in deck.pages)
        if not whole:
            return HeaderField()
    return HeaderField(value=value, refs=refs)


def _item(raw: LLMItem, deck: Deck) -> TeaserItem | None:
    text = " ".join(raw.text.split())
    if not text:
        return None
    return TeaserItem(
        section=raw.section,
        role=raw.role,
        text=text,
        refs=_refs(raw.pages, raw.quotes, deck),
        claim_type=raw.claim_type,
        extractor_category=raw.sensitive_category,
    )


def build_draft(llm: LLMDraft, deck: Deck, *, generated_by: str) -> TeaserDraft:
    """Convert model output into the editable draft, then verify and screen it."""
    headings = dict(DEFAULT_HEADINGS)
    for entry in llm.section_headings:
        if entry.heading.strip():
            headings[entry.section] = entry.heading.strip()[:60]
    draft = TeaserDraft(
        company_name=_header(llm.company_name, deck),
        tagline=_header(llm.tagline, deck),
        website=_header(llm.website, deck),
        industry=llm.industry,
        headings=headings,
        items=[i for i in (_item(r, deck) for r in llm.items) if i is not None],
        ingest_warnings=list(deck.warnings),
        generated_by=generated_by,
    )
    draft.brand = detect_brand(deck)
    apply_review(draft, deck, screen_deck(deck), llm.concerns)
    return draft


def generate_draft(deck: Deck, provider: Provider) -> TeaserDraft:
    """Run the provider and return a reviewed draft."""
    return build_draft(provider.extract(deck), deck, generated_by=provider.label)
