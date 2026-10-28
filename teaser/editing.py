"""Review state: flags for each item, approvals, re-checks after edits, and export gating.

The rules:

* An item whose text falls in an exclude-by-default category starts excluded. Including it
  requires approving its confidentiality flag.
* A funding ask is included but must be approved for distribution.
* Content drawn from slides marked confidential must be reviewed before export.
* A claim with no verifiable source, or a figure not found on its cited pages, must be
  confirmed (approved) or corrected before export.
* Qualifier drift, conflicts, and unclear statements are warnings: shown, never silent,
  but not export blockers.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from teaser.fidelity import check_claim, figures
from teaser.ingest.models import Deck
from teaser.schema import (
    EXCLUDE_BY_DEFAULT,
    SENSITIVE_LABELS,
    ClaimType,
    FlagKind,
    ItemRole,
    LLMConcern,
    ReviewFlag,
    SectionKey,
    SensitiveCategory,
    Severity,
    TeaserDraft,
    TeaserItem,
)
from teaser.screening import DeckScreen, categories_in

C = SensitiveCategory

_ROLE_CATEGORY = {
    ItemRole.COMMITTED_CAPITAL: C.COMMITTED_CAPITAL,
    ItemRole.FUNDING_ASK: C.FUNDING_ASK,
}
_FORECAST_WORDS = re.compile(
    r"\b(project\w*|forecast\w*|expect\w*|target\w*|plan\w*|anticipat\w*|estimat\w*|"
    r"potential|aim\w*|goal|will|by 20\d\d|upcoming)\b",
    re.IGNORECASE,
)


def _fmt_pages(pages: list[int]) -> str:
    if not pages:
        return "no page"
    label = "p." if len(pages) == 1 else "pp."
    return f"{label} {', '.join(str(p) for p in sorted(set(pages)))}"


def item_categories(item: TeaserItem) -> set[SensitiveCategory]:
    """Sensitive categories for an item: its text, its role, and the extractor's tag."""
    cats = categories_in(item.text)
    if item.role in _ROLE_CATEGORY:
        cats.add(_ROLE_CATEGORY[item.role])
    if item.extractor_category is not None:
        cats.add(item.extractor_category)
    return cats


def item_flags(item: TeaserItem, deck: Deck, *, confidential_pages: list[int], deck_confidential: bool) -> list[ReviewFlag]:
    """Every flag that applies to one item, computed from scratch."""
    flags: list[ReviewFlag] = []
    pages = item.pages

    for cat in sorted(item_categories(item), key=lambda c: c.value):
        if cat is C.FUNDING_ASK:
            msg = "Funding ask: confirm the amount is clearly stated and approved for distribution."
        elif cat in EXCLUDE_BY_DEFAULT:
            msg = f"{SENSITIVE_LABELS[cat]}. Excluded by default; approve only if cleared for public distribution."
        else:
            msg = SENSITIVE_LABELS[cat]
        flags.append(ReviewFlag(kind=FlagKind.CONFIDENTIALITY, severity=Severity.BLOCKING, category=cat,
                                pages=pages, item_id=item.id, message=msg))

    marked = sorted(set(pages) & set(confidential_pages))
    if marked:
        flags.append(ReviewFlag(
            kind=FlagKind.CONFIDENTIALITY,
            severity=Severity.WARNING if deck_confidential else Severity.BLOCKING,
            category=C.CONFIDENTIAL_MARKING, pages=marked, item_id=item.id,
            message=f"Drawn from {_fmt_pages(marked)}, marked confidential. Confirm it is approved for distribution.",
        ))

    if not item.refs:
        if item.user_added:
            flags.append(ReviewFlag(kind=FlagKind.UNSUPPORTED, severity=Severity.INFO, item_id=item.id,
                                    message="Added manually; no source reference. You are responsible for its accuracy."))
        else:
            flags.append(ReviewFlag(kind=FlagKind.UNSUPPORTED, severity=Severity.BLOCKING, item_id=item.id,
                                    message="No source page cited. Confirm against the deck or remove."))
        unmatched = [f.raw for f in figures(item.text)] if not item.user_added else []
        if unmatched:
            flags.append(ReviewFlag(kind=FlagKind.FIGURE_MISMATCH, severity=Severity.BLOCKING, item_id=item.id,
                                    message=f"Figure(s) {', '.join(unmatched)} have no source reference."))
        return flags

    result = check_claim(item.text, [(r.page, r.quote) for r in item.refs], deck)
    for ref, ok in zip(item.refs, result.verified_quotes, strict=True):
        ref.verified = ok
    if result.missing_pages:
        flags.append(ReviewFlag(kind=FlagKind.UNSUPPORTED, severity=Severity.BLOCKING, item_id=item.id,
                                pages=result.missing_pages,
                                message=f"Cites {_fmt_pages(result.missing_pages)}, which the deck does not have."))
    if not result.supported:
        flags.append(ReviewFlag(kind=FlagKind.UNSUPPORTED, severity=Severity.BLOCKING, item_id=item.id, pages=pages,
                                message=f"Supporting text not found on {_fmt_pages(pages)}. Verify against the deck."))
    if result.unmatched:
        flags.append(ReviewFlag(kind=FlagKind.FIGURE_MISMATCH, severity=Severity.BLOCKING, item_id=item.id, pages=pages,
                                message=f"Figure(s) {', '.join(result.unmatched)} not found on {_fmt_pages(pages)}. "
                                        f"Check value, unit, and currency."))
    for issue in result.drift:
        flags.append(ReviewFlag(kind=FlagKind.QUALIFIER_DRIFT, severity=Severity.WARNING, item_id=item.id,
                                pages=pages, message=issue))
    if (
        item.claim_type in (ClaimType.FORECAST, ClaimType.PLANNED)
        and item.role is not ItemRole.USE_OF_FUNDS  # a use of funds is a plan by definition
        and not _FORECAST_WORDS.search(item.text)
    ):
        flags.append(ReviewFlag(kind=FlagKind.QUALIFIER_DRIFT, severity=Severity.WARNING, item_id=item.id,
                                pages=pages, message=f"Marked as {item.claim_type.value}; the wording should say so."))
    return flags


def _key(flag: ReviewFlag) -> tuple[str, str, str]:
    return (flag.kind.value, flag.category.value if flag.category else "", flag.message)


def recheck_item(draft: TeaserDraft, item: TeaserItem, deck: Deck) -> None:
    """Recompute an item's flags after an edit, keeping approvals whose flag is unchanged."""
    approved = {_key(f) for f in draft.flags_for(item.id) if f.approved}
    fresh = item_flags(item, deck, confidential_pages=draft.confidential_pages,
                       deck_confidential=_deck_confidential(draft))
    for flag in fresh:
        flag.approved = _key(flag) in approved
    draft.flags = [f for f in draft.flags if f.item_id != item.id] + fresh


def _deck_confidential(draft: TeaserDraft) -> bool:
    return any(f.item_id is None and f.category is C.CONFIDENTIAL_MARKING and f.severity is Severity.BLOCKING
               for f in draft.flags)


def apply_review(
    draft: TeaserDraft, deck: Deck, screen: DeckScreen, concerns: Sequence[LLMConcern] = ()
) -> None:
    """Initial screening of a freshly built draft: flags, default exclusions, panel summary."""
    draft.confidential_pages = list(screen.confidential_pages)
    draft.unreadable_pages = deck.unreadable_pages

    if screen.deck_confidential:
        draft.flags.append(ReviewFlag(
            kind=FlagKind.CONFIDENTIALITY, severity=Severity.BLOCKING, category=C.CONFIDENTIAL_MARKING,
            pages=screen.confidential_pages,
            message="The deck is marked confidential. Confirm the company has approved distribution "
                    "of the selected content before exporting.",
        ))
    for item in draft.items:
        flags = item_flags(item, deck, confidential_pages=screen.confidential_pages,
                           deck_confidential=screen.deck_confidential)
        if any(f.category in EXCLUDE_BY_DEFAULT for f in flags):
            item.include = False
        draft.flags.extend(flags)

    # Page-level summary for the review panel: what was found where, by category only.
    for cat in C:
        pages = screen.pages_for(cat)
        if pages:
            draft.flags.append(ReviewFlag(
                kind=FlagKind.CONFIDENTIALITY, severity=Severity.INFO, category=cat, pages=pages,
                message=f"{SENSITIVE_LABELS[cat]} detected in the deck.",
            ))
    if screen.instruction_pages:
        draft.flags.append(ReviewFlag(
            kind=FlagKind.EMBEDDED_INSTRUCTION, severity=Severity.WARNING, pages=screen.instruction_pages,
            message="Text phrased as instructions to an AI system was found and ignored. Review these "
                    "pages and the draft closely.",
        ))
    if draft.unreadable_pages:
        draft.flags.append(ReviewFlag(
            kind=FlagKind.EXTRACTION, severity=Severity.WARNING, pages=draft.unreadable_pages,
            message="No readable text on these pages; their content is not reflected in the draft.",
        ))
    for label, pattern in _CONFLICT_CHECKS:
        found = _conflicting_figures(deck, pattern)
        if found:
            draft.flags.append(ReviewFlag(
                kind=FlagKind.CONFLICT, severity=Severity.WARNING, pages=found,
                message=f"Different {label} figures appear on {_fmt_pages(found)}. Confirm which is current.",
            ))
    seen = {(f.kind, f.category, tuple(f.pages)) for f in draft.flags if f.item_id is None}
    for concern in concerns:
        flag = _concern_flag(concern, deck)
        if flag.kind is FlagKind.CONFIDENTIALITY:
            # Already covered by the deterministic screen on any of the same pages?
            if any(k == flag.kind and c == flag.category and set(p) & set(flag.pages) for k, c, p in seen):
                continue
        elif (flag.kind, flag.category, tuple(flag.pages)) in seen and flag.kind is not FlagKind.UNCLEAR:
            continue
        seen.add((flag.kind, flag.category, tuple(flag.pages)))
        draft.flags.append(flag)


_AMOUNT = r"([$€£]?\s?\d[\d,.]*\s?(?:trillion|billion|million|bn|[KMBT])?)"
_CONFLICT_CHECKS = [
    ("TAM", re.compile(r"\bTAM\b[^\n\d$€£]{0,40}" + _AMOUNT)),
    ("SAM", re.compile(r"\bSAM\b[^\n\d$€£]{0,40}" + _AMOUNT)),
    ("SOM", re.compile(r"\bSOM\b[^\n\d$€£]{0,40}" + _AMOUNT)),
    ("funding ask", re.compile(r"\b(?:raising|seeking|raise of)\b[^\n\d$€£]{0,30}([$€£]\s?\d[\d,.]*\s?(?:million|billion|bn|[MKB])?)", re.I)),
]


def _concern_flag(concern: LLMConcern, deck: Deck) -> ReviewFlag:
    """Turn a concern reported by the extractor into a review flag.

    Confidentiality concerns become INFO entries in the panel (the deterministic screen and
    the item flags do the gating); the others are warnings. Page numbers are clamped to the
    deck so a hallucinated page cannot appear as a reference.
    """
    pages = [p for p in concern.pages if 1 <= p <= deck.page_count]
    if concern.kind == "confidentiality":
        label = SENSITIVE_LABELS[concern.category] if concern.category else "Possible confidential content"
        return ReviewFlag(kind=FlagKind.CONFIDENTIALITY, severity=Severity.INFO, category=concern.category,
                          pages=pages, message=f"{label} (reported by the extractor).")
    kind = {"conflict": FlagKind.CONFLICT, "unclear": FlagKind.UNCLEAR,
            "embedded_instruction": FlagKind.EMBEDDED_INSTRUCTION}[concern.kind]
    return ReviewFlag(kind=kind, severity=Severity.WARNING, pages=pages, message=concern.description[:400])


def _conflicting_figures(deck: Deck, pattern: re.Pattern[str]) -> list[int]:
    values: dict[float, set[int]] = {}
    for page in deck.pages:
        for match in pattern.finditer(page.text):
            parsed = figures(match.group(1))
            if parsed:
                values.setdefault(round(parsed[0].value, 2), set()).add(page.index)
    if len(values) < 2:
        return []
    return sorted({p for pages in values.values() for p in pages})


# --------------------------------------------------------------------------------------
# Export gating and editing helpers
# --------------------------------------------------------------------------------------


def export_blockers(draft: TeaserDraft) -> list[str]:
    """Reasons the teaser cannot be exported yet. Empty means ready."""
    reasons: list[str] = []
    if not draft.company_name.value.strip():
        reasons.append("Company name is missing.")
    for flag in draft.flags:
        if flag.severity is not Severity.BLOCKING or flag.approved:
            continue
        if flag.item_id is None:
            reasons.append(flag.message)
            continue
        item = draft.item(flag.item_id)
        if item is not None and item.include and item.text.strip():
            heading = draft.headings.get(item.section, item.section.value)
            reasons.append(f"{heading}: {flag.message}")
    if not any(i.include and i.text.strip() for i in draft.items):
        reasons.append("No content is selected for the teaser.")
    return reasons


def word_count(draft: TeaserDraft) -> int:
    """Words that will appear in the teaser body (tagline plus included items)."""
    parts = [draft.tagline.value if draft.tagline.include else ""]
    parts += [i.text for i in draft.items if i.include]
    return sum(len(p.split()) for p in parts)


def add_item(draft: TeaserDraft, section: SectionKey, deck: Deck, text: str = "") -> TeaserItem:
    """Append a user-written item to a section."""
    item = TeaserItem(section=section, text=text, user_added=True)
    draft.items.append(item)
    recheck_item(draft, item, deck)
    return item


def remove_item(draft: TeaserDraft, item_id: str) -> None:
    """Delete an item and its flags."""
    draft.items = [i for i in draft.items if i.id != item_id]
    draft.flags = [f for f in draft.flags if f.item_id != item_id]


def approve_confidential_pages(draft: TeaserDraft) -> int:
    """Bulk-approve every confidential-marking flag. Returns how many were approved."""
    count = 0
    for flag in draft.flags:
        if flag.category is C.CONFIDENTIAL_MARKING and not flag.approved and flag.severity is not Severity.INFO:
            flag.approved = True
            count += 1
    return count


def missing_sections(draft: TeaserDraft) -> list[SectionKey]:
    """Sections with no drafted content at all."""
    return [k for k in SectionKey if not draft.section_items(k)]
