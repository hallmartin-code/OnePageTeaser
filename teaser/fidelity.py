"""Source-fidelity checks: does each teaser claim say what its cited pages say?

Three checks, all against the extracted page text:

1. Quotes. Every quote the model cites must actually appear (fuzzily) on the cited page.
2. Figures. Every number in the teaser text, with its unit, magnitude, and currency, must
   appear on one of the cited pages. "$4.2B" against a page saying "$4.2M" fails.
3. Qualifiers. If the source wording is forward-looking, non-binding, or pre-approval,
   the teaser wording must not read as actual, signed, or approved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from teaser.ingest.models import Deck

QUOTE_MATCH_THRESHOLD = 88

_NUMBER = re.compile(
    r"(?P<cur>[$€£¥]|\b(?:USD|EUR|GBP|CAD|AUD|CHF)\s?)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<suf>%|percent\b|trillion\b|billion\b|million\b|thousand\b|bn\b|mm\b|"
    r"[KkMmBbTt](?![a-zA-Z])|x(?![a-zA-Z])))?"
)
_MULT = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "trillion": 1e12,
}
_CURRENCY = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
             "¥": "JPY", "cad": "CAD", "aud": "AUD", "chf": "CHF"}
_WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
}
_WORD_RE = re.compile(r"\b(" + "|".join(_WORD_NUMBERS) + r")\b", re.IGNORECASE)


@dataclass(frozen=True)
class Figure:
    """A number with its unit and magnitude resolved."""

    value: float
    kind: str  # "pct" | "mult" | "num"
    currency: str | None
    raw: str

    def matches(self, other: Figure) -> bool:
        """Same value, unit kind, and (where both state one) currency."""
        if self.kind != other.kind:
            # "40" in a table next to a "%" header is still 40%; allow pct vs bare num.
            if {self.kind, other.kind} != {"pct", "num"}:
                return False
        if self.currency and other.currency and self.currency != other.currency:
            return False
        if self.currency and not other.currency and other.kind == "num" and other.value < 1e3:
            # "$4.2B" must not be satisfied by a bare "4.2" somewhere on the page.
            return False
        tolerance = max(abs(self.value), abs(other.value)) * 1e-6
        return abs(self.value - other.value) <= tolerance


def figures(text: str) -> list[Figure]:
    """Every figure in ``text``."""
    text = _WORD_RE.sub(lambda m: _WORD_NUMBERS[m.group(1).lower()], text)
    out: list[Figure] = []
    for m in _NUMBER.finditer(text):
        num = float(m.group("num").replace(",", ""))
        suffix = (m.group("suf") or "").strip().lower()
        cur = (m.group("cur") or "").strip().lower()
        if suffix in ("%", "percent"):
            kind = "pct"
        elif suffix == "x":
            kind = "mult"
        else:
            kind = "num"
            num *= _MULT.get(suffix, 1.0)
        out.append(Figure(num, kind, _CURRENCY.get(cur), m.group(0).strip()))
    return out


def unmatched_figures(claim: str, sources: list[str]) -> list[str]:
    """Figures in ``claim`` that appear on none of the ``sources``."""
    pool = [f for s in sources for f in figures(s)]
    return [f.raw for f in figures(claim) if not any(f.matches(p) for p in pool)]


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def quote_on_page(quote: str, page_text: str) -> bool:
    """Whether ``quote`` appears on the page, tolerating extraction whitespace and hyphens."""
    q, p = _norm(quote), _norm(page_text)
    if not q or not p:
        return False
    if q in p:
        return True
    return fuzz.partial_ratio(q, p) >= QUOTE_MATCH_THRESHOLD


# Qualifier drift: (source signal, teaser wording that contradicts it, required teaser
# wording that would preserve it, message).
_FORWARD = re.compile(
    r"\b(project(ed|ion|ions)|forecast(ed|s)?|expect(ed|s)?|anticipat(ed|es)|"
    r"target(s|ed)? (of|revenue|date|launch|by)|plan(ned|s)? to|aim(s)? to|goal|"
    r"estimate[ds]?|by 20\d\d|pro forma|FY\d{2}E)\b",
    re.IGNORECASE,
)
_NONBINDING = re.compile(
    r"\b(LOIs?|letters? of intent|MOUs?|in (active )?discussions?|negotiat\w+|non-binding|"
    r"prospective|proposed|exploring|interest from|pending)\b",
    re.IGNORECASE,
)
_SIGNED = re.compile(r"\b(signed|contracted|secured|closed|executed|binding|partnered|customers?)\b", re.IGNORECASE)
_PREAPPROVAL = re.compile(
    r"\b(plan(ned|s)? to (submit|file)|(will|to) (submit|file)|submission (planned|expected|in)|"
    r"anticipated|expected (approval|clearance|submission)|pre-?(IND|submission|clinical)|"
    r"seeking|pursuing|pathway)\b",
    re.IGNORECASE,
)
_APPROVED = re.compile(r"\b(approved|cleared|clearance (received|granted|obtained)|authori[sz]ed|certified)\b", re.IGNORECASE)
_PLANNING_WORDS = re.compile(
    r"\b(plan\w*|expect\w*|target\w*|project\w*|forecast\w*|anticipat\w*|aim\w*|"
    r"potential|estimat\w*|goal|intend\w*|seek\w*|pursu\w*|will|by 20\d\d|upcoming|"
    r"proposed|prospective|LOIs?|letters? of intent|non-binding|discussions?|"
    r"negotiat\w*|MOUs?|pending|submission|submit\w*|file|filing)\b",
    re.IGNORECASE,
)


def qualifier_drift(claim: str, source: str) -> list[str]:
    """Ways in which the teaser wording is stronger than its source."""
    if _PLANNING_WORDS.search(claim):
        return []  # the teaser already carries a qualifier; leave tone to the reviewer
    issues: list[str] = []
    if _FORWARD.search(source) and figures(claim):
        issues.append("Source figures are forward-looking (forecast/target); the teaser states them as fact.")
    if _NONBINDING.search(source) and _SIGNED.search(claim):
        issues.append("Source describes discussions or non-binding intent; the teaser reads as signed or secured.")
    if _PREAPPROVAL.search(source) and _APPROVED.search(claim):
        issues.append("Source describes a planned regulatory step; the teaser reads as an approval.")
    return issues


@dataclass
class FidelityResult:
    """Outcome of checking one claim."""

    verified_quotes: list[bool]
    missing_pages: list[int]
    unmatched: list[str]
    drift: list[str]

    @property
    def supported(self) -> bool:
        """At least one cited quote was found on its page."""
        return any(self.verified_quotes)


def check_claim(text: str, refs: list[tuple[int, str | None]], deck: Deck) -> FidelityResult:
    """Check a claim against the pages and quotes it cites."""
    verified: list[bool] = []
    missing: list[int] = []
    sources: list[str] = []
    for page_no, quote in refs:
        page = deck.page(page_no)
        if page is None:
            missing.append(page_no)
            verified.append(False)
            continue
        sources.append(page.text)
        verified.append(bool(quote) and quote_on_page(quote or "", page.text))
    unmatched = unmatched_figures(text, sources) if sources else [f.raw for f in figures(text)]
    joined_quotes = " ".join(q for _, q in refs if q) or " ".join(sources)
    drift = qualifier_drift(text, joined_quotes)
    return FidelityResult(verified, missing, unmatched, drift)
