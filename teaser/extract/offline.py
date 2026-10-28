"""Offline extractive drafting: no model, nothing leaves the machine.

Pages are classified into teaser sections by their titles (falling back to body keywords),
and the most informative lines are lifted verbatim with their page references. Because
every item is a copy of deck text, every item verifies; the cost is that wording is the
deck's, not a summary, and the user is expected to edit for length.
"""

from __future__ import annotations

import re

from teaser.ingest.models import Deck, Page
from teaser.schema import (
    INDUSTRY_HEADINGS,
    ClaimType,
    ItemRole,
    LLMDraft,
    LLMField,
    LLMHeading,
    LLMItem,
    SectionKey,
)

S = SectionKey

_SECTION_KEYWORDS: list[tuple[SectionKey, re.Pattern[str]]] = [
    (S.FUNDING, re.compile(r"\b(the ask|our ask|investment ask|raising|use of (funds|proceeds)|funding|financing|the round|investment opportunity)\b", re.I)),
    (S.TEAM, re.compile(r"\b(team|leadership|founders?|management|advisors?|board)\b", re.I)),
    (S.MARKET_SIZE, re.compile(r"\b(market size|market opportunity|TAM|SAM|SOM|addressable market|market)\b", re.I)),
    (S.PIPELINE_PRODUCT, re.compile(r"\b(pipeline|programs?|portfolio|product roadmap|products?|assets?)\b", re.I)),
    (S.TRACTION, re.compile(r"\b(traction|milestones?|progress|achievements|validation|results|timeline|roadmap|clinical data)\b", re.I)),
    (S.SOLUTION_TECHNOLOGY, re.compile(r"\b(solution|technology|platform|how it works|approach|mechanism|innovation)\b", re.I)),
    (S.PROBLEM_MARKET, re.compile(r"\b(problem|challenge|unmet need|pain points?|why now|the need)\b", re.I)),
]
_SKIP_PAGE = re.compile(r"\b(disclaimer|forward[- ]looking|thank you|contact us|appendix|questions\?)\b", re.I)
_WEBSITE = re.compile(
    r"(?:https?://|\bwww\.)[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s]*)?"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|io|ai|bio|health|co|org|net|tech|us|uk|de|ca)\b(?:/[^\s]*)?",
    re.I,
)
_TAM = re.compile(r"\b(TAM|SAM|SOM)\b")
_ASK = re.compile(r"\b(raising|seeking|the ask|our ask)\b", re.I)
_USE = re.compile(r"\b(use of (funds|proceeds)|%|allocat)", re.I)
_FORECAST = re.compile(r"\b(projected|forecast|expected|target|plan(ned)? to|by 20\d\d|anticipated)\b", re.I)
_NUMBERISH = re.compile(r"\d")

MAX_ITEMS = {
    S.PROBLEM_MARKET: 4, S.SOLUTION_TECHNOLOGY: 4, S.PIPELINE_PRODUCT: 4, S.MARKET_SIZE: 4,
    S.TRACTION: 5, S.TEAM: 5, S.FUNDING: 5,
}

_INDUSTRY_SIGNALS = {
    "biotech": re.compile(r"\b(therapeutic|clinical|IND|FDA|preclinical|phase [1-3]|oncology|antibod|molecule)\b", re.I),
    "medtech": re.compile(r"\b(510\(k\)|device|diagnostic|CE mark|clearance)\b", re.I),
    "software": re.compile(r"\b(SaaS|ARR|MRR|subscription|platform users|API)\b", re.I),
}


def _classify(page: Page) -> SectionKey | None:
    title = page.title or ""
    if _SKIP_PAGE.search(title):
        return None
    for key, pattern in _SECTION_KEYWORDS:
        if pattern.search(title):
            return key
    best, hits = None, 0
    for key, pattern in _SECTION_KEYWORDS:
        count = len(pattern.findall(page.text))
        if count > hits:
            best, hits = key, count
    return best if hits >= 2 else None


def _lines(page: Page) -> list[str]:
    """Body lines, with wrapped lines rejoined and the title and table marker removed."""
    raw = [line.strip(" •·-–*\t") for line in page.text.split("\n")]
    raw = [line for line in raw if line and line != "[table]"]
    if raw and page.title and raw[0] == page.title.strip(" •·-–*\t"):
        raw = raw[1:]
    merged: list[str] = []
    for line in raw:
        if merged and merged[-1][-1:] not in ".:;!?" and line[:1].islower():
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)
    seen: set[str] = set()
    out: list[str] = []
    for line in merged:
        key = line.lower()
        if key not in seen and not _SKIP_PAGE.search(line) and not key.startswith(("source:", "sources:")):
            seen.add(key)
            out.append(line)
    return out


def _informative(line: str) -> bool:
    words = len(line.split())
    return words >= 5 or (words >= 2 and bool(_NUMBERISH.search(line)))


def _item(section: SectionKey, line: str, page: int) -> LLMItem:
    role = ItemRole.GENERAL
    if section is S.MARKET_SIZE and (m := _TAM.search(line)):
        role = ItemRole(m.group(1).lower())
    elif section is S.TEAM:
        role = ItemRole.TEAM_MEMBER
    elif section is S.FUNDING:
        role = ItemRole.FUNDING_ASK if _ASK.search(line) else ItemRole.USE_OF_FUNDS if _USE.search(line) else ItemRole.GENERAL
    claim = ClaimType.FORECAST if _FORECAST.search(line) else ClaimType.ACTUAL if _NUMBERISH.search(line) else ClaimType.DESCRIPTIVE
    return LLMItem(section=section, role=role, text=line, pages=[page], quotes=[line], claim_type=claim)


def _header(deck: Deck) -> tuple[LLMField, LLMField, LLMField]:
    cover = deck.pages[0]
    lines = [line for line in cover.text.split("\n") if line.strip()]
    name = LLMField(value=lines[0], pages=[1], quote=lines[0]) if lines else LLMField()
    tagline = LLMField()
    for line in lines[1:3]:
        if 3 <= len(line.split()) <= 16 and not _WEBSITE.search(line) and "@" not in line:
            tagline = LLMField(value=line, pages=[1], quote=line)
            break
    website = LLMField()
    for page in deck.pages:
        for line in page.text.split("\n"):
            if "@" in line:
                continue
            if m := _WEBSITE.search(line):
                website = LLMField(value=m.group(0), pages=[page.index], quote=m.group(0))
                break
        if website.value:
            break
    return name, tagline, website


class OfflineExtractor:
    """Verbatim, page-referenced extraction with no model call."""

    label = "Offline extractive mode"

    def extract(self, deck: Deck) -> LLMDraft:
        name, tagline, website = _header(deck)
        items: list[LLMItem] = []
        counts: dict[SectionKey, int] = {}
        for page in deck.pages[1:] if deck.page_count > 1 else deck.pages:
            section = _classify(page)
            if section is None:
                continue
            for line in _lines(page):
                if counts.get(section, 0) >= MAX_ITEMS[section]:
                    break
                if _informative(line):
                    items.append(_item(section, line, page.index))
                    counts[section] = counts.get(section, 0) + 1
        industry = None
        all_text = "\n".join(p.text for p in deck.pages)
        scores = {k: len(p.findall(all_text)) for k, p in _INDUSTRY_SIGNALS.items()}
        if scores and max(scores.values()) >= 3:
            industry = max(scores, key=lambda k: scores[k])
        headings = [
            LLMHeading(section=k, heading=v) for k, v in INDUSTRY_HEADINGS.get(industry or "", {}).items()
        ]
        return LLMDraft(
            company_name=name,
            tagline=tagline,
            website=website,
            industry=industry,
            section_headings=headings,
            items=items,
        )
