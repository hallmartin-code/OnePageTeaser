"""Rendering: one-page PDF (primary) and editable DOCX (optional)."""

from teaser.render.pdf import (
    RenderOptions,
    RenderResult,
    layout_audit,
    page_count,
    preview_png,
    render_pdf,
)

__all__ = ["RenderOptions", "RenderResult", "layout_audit", "page_count", "preview_png", "render_pdf"]
