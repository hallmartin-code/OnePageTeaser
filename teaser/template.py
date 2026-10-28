"""The document structure template (``templates/teaser_template.json``), validated.

Section order, default headings, industry heading variants, the disclaimer, typography
limits, page geometry, and word targets all come from here, so changing the teaser's
structure is a template edit rather than a code change. This module deliberately does not
import ``teaser.schema``; ``schema`` checks that its ``SectionKey`` enum matches the
template's section keys.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "teaser_template.json"


class _Range(BaseModel):
    min: float
    max: float


class _BodySize(_Range):
    step: float = 0.5


class Margins(BaseModel):
    left: float
    right: float
    top: float
    bottom: float


class PageSpec(BaseModel):
    size: str
    orientation: str
    max_pages: int = Field(ge=1, le=1)
    margins_in: Margins
    body_columns: int = Field(ge=1, le=2)
    column_gutter_in: float


class Typography(BaseModel):
    font_family: str
    fallback_font: str
    body_pt: _BodySize
    line_height: float
    company_name_pt: _Range
    tagline_pt: float
    website_pt: float
    disclaimer_pt: float
    footer_pt: float

    @property
    def body_sizes(self) -> tuple[float, ...]:
        """Candidate body sizes, largest first."""
        sizes, size = [], self.body_pt.max
        while size >= self.body_pt.min - 1e-9:
            sizes.append(round(size, 2))
            size -= self.body_pt.step
        return tuple(sizes)


class SectionSpec(BaseModel):
    key: str
    default_heading: str
    purpose: str
    content: list[str]
    item_roles: list[str]
    format: str
    excluded_roles: list[str] = Field(default_factory=list)
    approval_required: str | None = None


class FooterSpec(BaseModel):
    disclaimer: str
    disclaimer_editable: bool = False


class LengthSpec(BaseModel):
    target_words: _Range
    item_words: _Range


class Template(BaseModel):
    template_id: str
    version: str
    page: PageSpec
    typography: Typography
    content_length: LengthSpec
    sections: list[SectionSpec]
    industry_heading_variants: dict[str, dict[str, str]]
    footer: FooterSpec

    @model_validator(mode="after")
    def _consistent(self) -> Template:
        keys = [s.key for s in self.sections]
        if len(keys) != len(set(keys)):
            raise ValueError("Template section keys must be unique.")
        for industry, variants in self.industry_heading_variants.items():
            unknown = set(variants) - set(keys)
            if unknown:
                raise ValueError(f"Heading variants for {industry!r} name unknown sections: {sorted(unknown)}")
        if self.typography.body_pt.min < 9.0:
            raise ValueError("Body text may never be below 9 pt.")
        return self

    @property
    def section_keys(self) -> list[str]:
        """Section keys in reading order."""
        return [s.key for s in self.sections]


@functools.cache
def load_template(path: Path = TEMPLATE_PATH) -> Template:
    """Load and validate the template. Cached: it is read once per process."""
    return Template.model_validate_json(path.read_text(encoding="utf-8"))
