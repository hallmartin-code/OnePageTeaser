"""Export the reviewed teaser: PDF, DOCX, PNG, a review record, or all of them as a ZIP.

Every format is built from the same ``TeaserDraft``, so they always carry identical content:
included items only. The review record is the audit trail: what was published, which deck
pages back each statement, and which flags a reviewer approved. Excluded items appear in it
by category and page only, never by text.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any

from teaser.editing import export_blockers
from teaser.render import RenderOptions, preview_png, render_pdf
from teaser.render.docx_export import render_docx
from teaser.render.pdf import ORDER
from teaser.schema import (
    EXCLUDE_BY_DEFAULT,
    SENSITIVE_LABELS,
    TEMPLATE,
    FlagKind,
    Severity,
    TeaserDraft,
)

DRAFT_WATERMARK = "DRAFT — NOT FOR DISTRIBUTION"
PNG_DPI = 200


@dataclass(frozen=True)
class SourceInfo:
    """Where the teaser came from, for the review record."""

    deck_filename: str
    page_count: int
    unreadable_pages: list[int]
    generated_by: str
    generated_at: dt.datetime | None = None


@dataclass(frozen=True)
class ExportBundle:
    """Every export format for one approved teaser."""

    stem: str
    pdf: bytes
    docx: bytes
    png: bytes
    record: bytes

    def files(self) -> dict[str, tuple[bytes, str]]:
        """filename -> (content, MIME type)."""
        return {
            f"{self.stem}.pdf": (self.pdf, "application/pdf"),
            f"{self.stem}.docx": (self.docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            f"{self.stem}.png": (self.png, "image/png"),
            f"{self.stem}_review_record.json": (self.record, "application/json"),
        }

    def zip(self) -> bytes:
        """All formats in one archive."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, (content, _) in self.files().items():
                archive.writestr(name, content)
        return buffer.getvalue()


def file_stem(draft: TeaserDraft, *, draft_copy: bool = False) -> str:
    """``<Company>_Investor_Teaser`` with filesystem-safe characters."""
    name = "".join(ch if ch.isalnum() else "_" for ch in draft.company_name.value.strip())
    name = "_".join(part for part in name.split("_") if part) or "Company"
    return f"{name}_Investor_Teaser" + ("_DRAFT" if draft_copy else "")


def review_record(draft: TeaserDraft, source: SourceInfo, *, body_pt: float, words: int,
                  exported_at: dt.datetime | None = None) -> dict[str, Any]:
    """The audit trail for an exported teaser."""
    exported_at = exported_at or dt.datetime.now(dt.UTC)
    sections = []
    for key in ORDER:
        items = [i for i in draft.section_items(key, included_only=True) if i.text.strip()]
        if not items:
            continue
        sections.append({
            "key": key.value,
            "heading": draft.headings.get(key, key.value),
            "items": [
                {
                    "text": item.text,
                    "role": item.role.value,
                    "claim_type": item.claim_type.value,
                    "added_by_reviewer": item.user_added,
                    "sources": [{"page": r.page, "quote": r.quote, "verified": r.verified} for r in item.refs],
                }
                for item in items
            ],
        })

    approvals = []
    for flag in draft.flags:
        if flag.severity is not Severity.BLOCKING or not flag.approved:
            continue
        item = draft.item(flag.item_id) if flag.item_id else None
        if flag.item_id and not (item and item.include):
            continue
        approvals.append({
            "scope": "item" if item else "deck",
            "item": item.text if item else None,
            "kind": flag.kind.value,
            "category": flag.category.value if flag.category else None,
            "pages": flag.pages,
            "reason": flag.message,
        })

    excluded = []
    for item in draft.items:
        if item.include:
            continue
        cats = sorted({f.category for f in draft.flags_for(item.id) if f.category in EXCLUDE_BY_DEFAULT},
                      key=lambda c: c.value)
        excluded.append({
            "section": item.section.value,
            "pages": item.pages,
            "categories": [SENSITIVE_LABELS[c] for c in cats] or ["Excluded by reviewer"],
        })

    warnings = [
        {"kind": f.kind.value, "pages": f.pages, "message": f.message}
        for f in draft.flags
        if f.item_id is None and f.kind in (FlagKind.CONFLICT, FlagKind.UNCLEAR,
                                            FlagKind.EMBEDDED_INSTRUCTION, FlagKind.EXTRACTION)
    ]

    return {
        "document": "One-page investor teaser",
        "template": {"id": TEMPLATE.template_id, "version": TEMPLATE.version},
        "company": {
            "name": draft.company_name.value,
            "tagline": draft.tagline.value if draft.tagline.include else None,
            "website": draft.website.value if draft.website.include else None,
        },
        "source": {
            "deck_filename": source.deck_filename,
            "pages": source.page_count,
            "unreadable_pages": source.unreadable_pages,
            "generated_by": source.generated_by,
            "generated_at": source.generated_at.isoformat() if source.generated_at else None,
        },
        "exported_at": exported_at.isoformat(),
        "layout": {"pages": 1, "page_size": "US Letter", "body_pt": body_pt, "words": words},
        "sections": sections,
        "approvals": approvals,
        "excluded_items": excluded,
        "warnings": warnings,
        "disclaimer": draft.disclaimer,
    }


def build_bundle(draft: TeaserDraft, source: SourceInfo, options: RenderOptions) -> ExportBundle:
    """Render every format for an approved teaser. Refuses while approvals are open."""
    blockers = export_blockers(draft)
    if blockers:
        raise ValueError(f"{len(blockers)} approval(s) are still open; the teaser cannot be exported.")
    result = render_pdf(draft, options)
    record = review_record(draft, source, body_pt=result.report.body_size, words=result.report.words)
    return ExportBundle(
        stem=file_stem(draft),
        pdf=result.pdf,
        docx=render_docx(draft, options),
        png=preview_png(result.pdf, dpi=PNG_DPI),
        record=json.dumps(record, indent=2, ensure_ascii=False).encode("utf-8"),
    )


def draft_pdf(draft: TeaserDraft, options: RenderOptions) -> bytes:
    """A watermarked PDF of the teaser as it stands, for internal review before approval."""
    watermarked = RenderOptions(ten_footer=options.ten_footer, include_logo=options.include_logo,
                                compiled_on=options.compiled_on, watermark=DRAFT_WATERMARK)
    return render_pdf(draft, watermarked).pdf
