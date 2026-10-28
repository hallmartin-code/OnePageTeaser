"""Deterministic confidentiality screening.

Runs on every deck page and every teaser item regardless of what the model said, so a
model that misses a category (or is talked out of flagging one by text inside the deck)
does not get the last word. Patterns err towards flagging: a false positive costs the
user one click; a false negative can put a valuation in front of every investor on a list.

Nothing in this module returns matched text. Results are categories and page numbers, so
the review panel can describe what was found without reproducing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from teaser.ingest.models import Deck
from teaser.schema import SensitiveCategory as C

_I = re.IGNORECASE

PATTERNS: dict[C, list[re.Pattern[str]]] = {
    C.INVESTOR_IDENTITY: [
        re.compile(
            r"\b(led by|lead investor|co-?led|co-?lead|investors? include|backed by|"
            r"participation from|existing investors|strategic investors?|anchor investor|"
            r"invested by|investment from|syndicate (members|partners))\b",
            _I,
        ),
        # Firm-like names: "Northwind Ventures", "Acme Capital Partners".
        re.compile(r"\b[A-Z][\w&'.-]+(?:\s[A-Z][\w&'.-]+){0,3}\s(Ventures|Capital|Partners|VC|Angels|Fund [IVX]+)\b"),
    ],
    C.COMMITTED_CAPITAL: [
        re.compile(
            r"\b(committed|commitments?|soft[- ]circled?|oversubscribed|raised to date|"
            r"already raised|have raised|has raised|we raised|closed (on )?\$|has invested|"
            r"subscribed|indications? of interest|reserved allocation|"
            r"remaining to (be )?raise|left to raise|balance of the round)\b",
            _I,
        ),
    ],
    C.DEAL_TERMS: [
        re.compile(
            r"\b(pre-?money|post-?money|valuation|cap table|capitali[sz]ation table|"
            r"liquidation preference|pro[- ]rata|warrants?|option pool|fully[- ]diluted|"
            r"term sheet|price per share|share price|convertible note|note maturity|"
            r"maturity date|interest rate|conversion (price|discount)|ownership stake|"
            r"preferred stock|series [a-d] preferred)\b",
            _I,
        ),
        re.compile(r"\bSAFEs?\b"),  # uppercase only: "safe and effective" is not a SAFE
        re.compile(r"\b\d{1,2}(\.\d+)?\s?% (discount|ownership|equity|stake)\b", _I),
        re.compile(r"\b(valuation )?cap of\b", _I),
    ],
    C.UNDISCLOSED_NEGOTIATION: [
        re.compile(
            r"\b(in (active |advanced |ongoing )?(discussions?|negotiations?|talks)|negotiating|"
            r"under NDA|letters? of intent|LOIs?|MOUs?|memorand(um|a) of understanding|"
            r"non-binding|pending (agreement|contract|close)|in diligence|expected to close|"
            r"exploring (a )?partnership|term sheet)\b",
            _I,
        ),
    ],
    C.CUSTOMER_IDENTITY: [
        re.compile(
            r"\b(customers? include|clients? include|customers?:|clients?:|pilots? with|"
            r"piloting with|partnered with|partnership with|signed with|contracts? with|"
            r"agreements? with|deployed (at|with)|purchase orders? from|POs? from|"
            r"trusted by|used by)\b",
            _I,
        ),
    ],
    C.PROPRIETARY_TECHNICAL: [
        re.compile(
            r"\b(chemical structure|molecular structure|compound structure|structure of "
            r"(the )?compound|recipe|proprietary formula(tion)?|exact formulation|"
            r"formulation details|synthe(sis|tic) route|process parameters|"
            r"amino acid sequence|nucleotide sequence|SEQ ID|source code)\b",
            _I,
        ),
        re.compile(r"\b[ACGTU]{15,}\b"),  # nucleotide sequence
        re.compile(r"\b[ACDEFGHIKLMNPQRSTVWY]{20,}\b"),  # peptide sequence
        re.compile(r"\bC\d{1,3}H\d{1,3}(?:[A-Z][a-z]?\d{0,3}){1,6}\b"),  # molecular formula
        re.compile(r"(?=\S*[=#])(?=\S*\()[A-Za-z0-9@+\-\[\]()=#/\\]{12,}"),  # SMILES-like
    ],
    C.IP_STRATEGY: [
        re.compile(
            r"\b(provisional (patent )?applications?|patent application (no\.?|number|serial)|"
            r"application (no\.?|serial)|PCT/[A-Z]{2}|unpublished|not yet published|"
            r"filing strategy|IP strategy|continuation[- ]in[- ]part|freedom[- ]to[- ]operate|"
            r"FTO|priority date|claims (cover|directed to)|patent claims)\b",
            _I,
        ),
        re.compile(r"\b(US|WO|EP)\s?\d{2,4}[/ ]\d{3}[, ]?\d{3}\b"),
    ],
    C.CONTACT_DETAILS: [
        re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"),
        re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
        re.compile(r"\b\d{1,5}\s(\w+\s){1,3}(Street|St\.|Avenue|Ave\.|Road|Rd\.|Suite|Blvd\.?)\b"),
        re.compile(r"\blinkedin\.com/in/\S+", _I),
    ],
}

CONFIDENTIAL_MARK = re.compile(
    r"\b(strictly )?confidential\b|\bdo not (distribute|forward|share|copy)\b|"
    r"\bnot for (distribution|circulation)\b|\bunder nda\b|\bprivileged\b",
    _I,
)

EMBEDDED_INSTRUCTION = re.compile(
    r"\b(ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|rules)|"
    r"disregard (all |the )?(previous|prior|above|system)|system prompt|"
    r"you are (now )?(an?|the) (ai|assistant|language model)|"
    r"(override|bypass) (the |all )?(instructions|rules|safeguards)|"
    r"do not (flag|exclude|redact|screen)|include (all|every) (investor|valuation))\b",
    _I,
)

#: Categories where a funding page's context makes firm-like names investor names.
_FUNDING_CONTEXT = re.compile(r"\b(round|raise|raising|invest|investor|commit|funding|seed|series)\b", _I)


def categories_in(text: str) -> set[C]:
    """Every sensitive category whose patterns match ``text``.

    A firm-like name ("... Partners", "... Capital") only counts as an investor identity
    when funding language is nearby; on its own it is more often a customer or partner,
    which the customer patterns cover.
    """
    found: set[C] = set()
    for category, patterns in PATTERNS.items():
        if any(p.search(text) for p in patterns):
            found.add(category)
    if (
        C.INVESTOR_IDENTITY in found
        and not PATTERNS[C.INVESTOR_IDENTITY][0].search(text)
        and not _FUNDING_CONTEXT.search(text)
    ):
        found.discard(C.INVESTOR_IDENTITY)
    return found


def is_marked_confidential(text: str) -> bool:
    """Whether the text carries a confidentiality marking."""
    return bool(CONFIDENTIAL_MARK.search(text))


def has_embedded_instruction(text: str) -> bool:
    """Whether the text contains something phrased as an instruction to an AI system."""
    return bool(EMBEDDED_INSTRUCTION.search(text))


@dataclass
class DeckScreen:
    """Page-level screening results."""

    categories_by_page: dict[int, set[C]] = field(default_factory=dict)
    confidential_pages: list[int] = field(default_factory=list)
    instruction_pages: list[int] = field(default_factory=list)
    deck_confidential: bool = False

    def pages_for(self, category: C) -> list[int]:
        """Pages on which a category was detected."""
        return sorted(p for p, cats in self.categories_by_page.items() if category in cats)


def screen_deck(deck: Deck) -> DeckScreen:
    """Screen every page of the deck."""
    result = DeckScreen()
    for page in deck.pages:
        cats = categories_in(page.text)
        if cats:
            result.categories_by_page[page.index] = cats
        if is_marked_confidential(page.text):
            result.confidential_pages.append(page.index)
        if has_embedded_instruction(page.text):
            result.instruction_pages.append(page.index)
    marked = len(result.confidential_pages)
    result.deck_confidential = bool(marked) and (
        1 in result.confidential_pages or marked * 2 >= max(deck.page_count, 1)
    )
    return result
