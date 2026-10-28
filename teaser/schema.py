"""Validated data model for teaser content, source references, and review flags.

Two layers live here:

* ``LLMDraft`` and friends: the shape the model is asked to return. Kept flat and simple
  so structured-output schemas stay within what providers support.
* ``TeaserDraft`` and friends: the editable working state. Built from an ``LLMDraft`` (or
  the offline extractor), then verified against the deck, screened, and edited by the user.
  Only a ``TeaserDraft`` is ever rendered.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from teaser.template import load_template

TEMPLATE = load_template()
DISCLAIMER = TEMPLATE.footer.disclaimer


class SectionKey(StrEnum):
    """The teaser's sections, in reading order (must match the template)."""

    PROBLEM_MARKET = "problem_market"
    SOLUTION_TECHNOLOGY = "solution_technology"
    PIPELINE_PRODUCT = "pipeline_product"
    MARKET_SIZE = "market_size"
    TRACTION = "traction"
    TEAM = "team"
    FUNDING = "funding"


if [k.value for k in SectionKey] != TEMPLATE.section_keys:
    raise RuntimeError(
        f"templates/teaser_template.json sections {TEMPLATE.section_keys} do not match "
        f"SectionKey {[k.value for k in SectionKey]} (same keys, same order)."
    )

DEFAULT_HEADINGS: dict[SectionKey, str] = {SectionKey(s.key): s.default_heading for s in TEMPLATE.sections}

#: What each section must contain, shown in the review UI when the section is empty.
SECTION_PURPOSE: dict[SectionKey, str] = {SectionKey(s.key): s.purpose for s in TEMPLATE.sections}

#: Industry-specific heading overrides, keyed by industry label.
INDUSTRY_HEADINGS: dict[str, dict[SectionKey, str]] = {
    industry: {SectionKey(k): v for k, v in variants.items()}
    for industry, variants in TEMPLATE.industry_heading_variants.items()
}


class ClaimType(StrEnum):
    """What kind of statement an item makes. Forecasts must stay forecasts."""

    ACTUAL = "actual"
    FORECAST = "forecast"
    PLANNED = "planned"
    DESCRIPTIVE = "descriptive"


class ItemRole(StrEnum):
    """Finer role of an item inside its section, where the distinction matters."""

    GENERAL = "general"
    TAM = "tam"
    SAM = "sam"
    SOM = "som"
    FUNDING_ASK = "funding_ask"
    USE_OF_FUNDS = "use_of_funds"
    COMMITTED_CAPITAL = "committed_capital"
    TEAM_MEMBER = "team_member"


class SensitiveCategory(StrEnum):
    """Confidentiality categories excluded or gated by default."""

    INVESTOR_IDENTITY = "investor_identity"
    COMMITTED_CAPITAL = "committed_capital"
    UNDISCLOSED_NEGOTIATION = "undisclosed_negotiation"
    DEAL_TERMS = "deal_terms"
    PROPRIETARY_TECHNICAL = "proprietary_technical"
    IP_STRATEGY = "ip_strategy"
    CONTACT_DETAILS = "contact_details"
    CUSTOMER_IDENTITY = "customer_identity"
    CONFIDENTIAL_MARKING = "confidential_marking"
    FUNDING_ASK = "funding_ask"


SENSITIVE_LABELS: dict[SensitiveCategory, str] = {
    SensitiveCategory.INVESTOR_IDENTITY: "Investor names / identities",
    SensitiveCategory.COMMITTED_CAPITAL: "Committed or individual investment amounts",
    SensitiveCategory.UNDISCLOSED_NEGOTIATION: "Undisclosed financing or commercial negotiations",
    SensitiveCategory.DEAL_TERMS: "Deal terms, valuation, or cap table details",
    SensitiveCategory.PROPRIETARY_TECHNICAL: "Proprietary structures, formulas, sequences, or recipes",
    SensitiveCategory.IP_STRATEGY: "Detailed IP strategy or unpublished patent information",
    SensitiveCategory.CONTACT_DETAILS: "Personal contact details",
    SensitiveCategory.CUSTOMER_IDENTITY: "Customer or partner identities",
    SensitiveCategory.CONFIDENTIAL_MARKING: "Content marked confidential",
    SensitiveCategory.FUNDING_ASK: "Funding ask (needs approval for distribution)",
}

#: Categories whose items start excluded. FUNDING_ASK and CONFIDENTIAL_MARKING start
#: included but cannot be exported until approved.
EXCLUDE_BY_DEFAULT: frozenset[SensitiveCategory] = frozenset(
    {
        SensitiveCategory.INVESTOR_IDENTITY,
        SensitiveCategory.COMMITTED_CAPITAL,
        SensitiveCategory.UNDISCLOSED_NEGOTIATION,
        SensitiveCategory.DEAL_TERMS,
        SensitiveCategory.PROPRIETARY_TECHNICAL,
        SensitiveCategory.IP_STRATEGY,
        SensitiveCategory.CONTACT_DETAILS,
        SensitiveCategory.CUSTOMER_IDENTITY,
    }
)


# --------------------------------------------------------------------------------------
# Model output
# --------------------------------------------------------------------------------------


class LLMField(BaseModel):
    """A single header value with its evidence."""

    value: str | None = Field(default=None, description="Exactly as stated in the deck, or null.")
    pages: list[int] = Field(default_factory=list, description="1-based page/slide numbers.")
    quote: str | None = Field(default=None, description="Short verbatim excerpt supporting it.")


class LLMItem(BaseModel):
    """One concise teaser bullet or sentence."""

    section: SectionKey
    role: ItemRole = ItemRole.GENERAL
    text: str = Field(description="Concise teaser wording. Facts only from the cited pages.")
    pages: list[int] = Field(default_factory=list, description="1-based pages supporting it.")
    quotes: list[str] = Field(
        default_factory=list, description="Verbatim excerpts from those pages backing every fact."
    )
    claim_type: ClaimType = ClaimType.DESCRIPTIVE
    sensitive_category: SensitiveCategory | None = Field(
        default=None, description="Set when the item touches a confidentiality category."
    )


class LLMConcern(BaseModel):
    """A confidentiality concern or a data-quality issue the model noticed."""

    kind: Literal["confidentiality", "conflict", "unclear", "embedded_instruction"]
    category: SensitiveCategory | None = None
    pages: list[int] = Field(default_factory=list)
    description: str = Field(
        description="Neutral description. Do not repeat sensitive values, names, or terms."
    )


class LLMHeading(BaseModel):
    """An industry-appropriate heading for one section."""

    section: SectionKey
    heading: str


class LLMDraft(BaseModel):
    """Everything the model returns in one call."""

    company_name: LLMField = Field(default_factory=LLMField)
    tagline: LLMField = Field(default_factory=LLMField)
    website: LLMField = Field(default_factory=LLMField)
    industry: str | None = Field(default=None, description="Short industry label, e.g. 'biotech'.")
    section_headings: list[LLMHeading] = Field(
        default_factory=list,
        description="Optional industry-appropriate heading per section.",
    )
    items: list[LLMItem] = Field(default_factory=list)
    concerns: list[LLMConcern] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Editable working state
# --------------------------------------------------------------------------------------


def new_id(prefix: str) -> str:
    """A short random identifier, stable across Streamlit reruns once assigned."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class SourceRef(BaseModel):
    """Where in the deck a claim comes from."""

    page: int = Field(ge=1)
    quote: str | None = None
    verified: bool = Field(default=False, description="Quote was found on that page.")


class Severity(StrEnum):
    """How a flag affects export."""

    BLOCKING = "blocking"  # export refused until the user approves or excludes the item
    WARNING = "warning"  # shown prominently; does not block
    INFO = "info"


class FlagKind(StrEnum):
    """What a review flag is about."""

    CONFIDENTIALITY = "confidentiality"
    UNSUPPORTED = "unsupported"
    FIGURE_MISMATCH = "figure_mismatch"
    QUALIFIER_DRIFT = "qualifier_drift"
    CONFLICT = "conflict"
    UNCLEAR = "unclear"
    EMBEDDED_INSTRUCTION = "embedded_instruction"
    EXTRACTION = "extraction"


class ReviewFlag(BaseModel):
    """A warning or approval requirement shown in the review interface.

    ``message`` is deliberately neutral: the review panel names categories and page
    numbers, never the sensitive text itself.
    """

    id: str = Field(default_factory=lambda: new_id("flag"))
    kind: FlagKind
    severity: Severity
    message: str
    category: SensitiveCategory | None = None
    pages: list[int] = Field(default_factory=list)
    item_id: str | None = None
    approved: bool = False

    @field_validator("pages")
    @classmethod
    def _sorted_unique(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


class TeaserItem(BaseModel):
    """One editable bullet."""

    id: str = Field(default_factory=lambda: new_id("item"))
    section: SectionKey
    role: ItemRole = ItemRole.GENERAL
    text: str
    refs: list[SourceRef] = Field(default_factory=list)
    claim_type: ClaimType = ClaimType.DESCRIPTIVE
    extractor_category: SensitiveCategory | None = Field(
        default=None, description="Category the model assigned; re-applied on every re-check."
    )
    include: bool = True
    user_added: bool = False

    @property
    def pages(self) -> list[int]:
        """Sorted unique pages this item cites."""
        return sorted({ref.page for ref in self.refs})


class HeaderField(BaseModel):
    """Company name, tagline, or website."""

    value: str = ""
    refs: list[SourceRef] = Field(default_factory=list)
    include: bool = True


class Brand(BaseModel):
    """Colours and logo used by the renderer."""

    primary: str = "#1F3A5F"  # restrained navy fallback
    accent: str = "#4A6F94"
    detected: bool = False
    source: str = "default palette"
    logo_png: bytes | None = None


class TeaserDraft(BaseModel):
    """The complete editable teaser plus everything the review panel needs."""

    company_name: HeaderField = Field(default_factory=HeaderField)
    tagline: HeaderField = Field(default_factory=HeaderField)
    website: HeaderField = Field(default_factory=HeaderField)
    industry: str | None = None
    headings: dict[SectionKey, str] = Field(default_factory=lambda: dict(DEFAULT_HEADINGS))
    items: list[TeaserItem] = Field(default_factory=list)
    flags: list[ReviewFlag] = Field(default_factory=list)
    brand: Brand = Field(default_factory=Brand)
    disclaimer: str = DISCLAIMER
    confidential_pages: list[int] = Field(default_factory=list)
    unreadable_pages: list[int] = Field(default_factory=list)
    ingest_warnings: list[str] = Field(default_factory=list)
    generated_by: str = ""

    def section_items(self, key: SectionKey, *, included_only: bool = False) -> list[TeaserItem]:
        """Items for one section, in order."""
        return [i for i in self.items if i.section == key and (i.include or not included_only)]

    def item(self, item_id: str) -> TeaserItem | None:
        """Look up an item by id."""
        return next((i for i in self.items if i.id == item_id), None)

    def flags_for(self, item_id: str) -> list[ReviewFlag]:
        """All flags attached to an item."""
        return [f for f in self.flags if f.item_id == item_id]

    @field_validator("disclaimer")
    @classmethod
    def _disclaimer_is_fixed(cls, value: str) -> str:
        if value != DISCLAIMER:
            raise ValueError("The disclaimer text is fixed and cannot be edited.")
        return value
