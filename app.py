"""Streamlit interface: upload a deck, review and edit a teaser, export a one-page PDF.

    streamlit run app.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import uuid

import streamlit as st
from PIL import Image

from teaser import export, mailer, ui
from teaser.config import PROJECT_ROOT, Settings, load_settings
from teaser.editing import (
    add_item,
    approve_confidential_pages,
    export_blockers,
    missing_sections,
    recheck_item,
    remove_item,
    word_count,
)
from teaser.errors import (
    ConfigError,
    LayoutOverflowError,
    PasswordRequiredError,
    TeaserError,
)
from teaser.extract import generate_draft, make_provider
from teaser.extract.fixture import FixtureProvider
from teaser.extract.offline import OfflineExtractor
from teaser.extract.providers import check_connection
from teaser.ingest import load_deck
from teaser.ingest.ocr import ocr_available
from teaser.render import RenderOptions, layout_audit, preview_png, render_pdf
from teaser.schema import (
    EXCLUDE_BY_DEFAULT,
    SECTION_PURPOSE,
    SENSITIVE_LABELS,
    TEMPLATE,
    FlagKind,
    SectionKey,
    SensitiveCategory,
    Severity,
    TeaserDraft,
    TeaserItem,
)

SAMPLE_DECK = PROJECT_ROOT / "samples" / "sample_deck.pdf"
WORD_TARGET = (int(TEMPLATE.content_length.target_words.min), int(TEMPLATE.content_length.target_words.max))

st.set_page_config(page_title="TEN Capital · Deck to One-Pager", page_icon=str(PROJECT_ROOT / "static" / "favicon.png"),
                   layout="wide")


# --------------------------------------------------------------------------------------
# State helpers
# --------------------------------------------------------------------------------------


def _reset() -> None:
    for key in ("deck", "draft", "deck_key", "password_needed", "sample", "email_status", "run_key", "run_info", "approval"):
        st.session_state.pop(key, None)


def _included(draft: TeaserDraft, item_id: str | None) -> bool:
    item = draft.item(item_id) if item_id else None
    return bool(item and item.include)


def _bound_checkbox(label: str, key: str, model_value: bool, **kwargs: object) -> bool:
    """A checkbox whose state starts from the draft and never overrides later model changes.

    Seeding session state (instead of passing ``value=``) lets callbacks such as bulk
    approval update the widget before the next run, so the draft stays the source of truth.
    """
    if key not in st.session_state:
        st.session_state[key] = model_value
    return st.checkbox(label, key=key, **kwargs)  # type: ignore[arg-type]


def _approve_confidential_callback() -> None:
    draft: TeaserDraft = st.session_state.draft
    approve_confidential_pages(draft)
    for flag in draft.flags:
        if flag.category is SensitiveCategory.CONFIDENTIAL_MARKING:
            prefix = "deck-approve" if flag.item_id is None else "approve"
            st.session_state[f"{prefix}-{flag.id}"] = flag.approved


def _settings() -> Settings | None:
    try:
        return load_settings()
    except ConfigError as exc:
        st.error(str(exc))
        return None


def _pages(pages: list[int]) -> str:
    if not pages:
        return "—"
    return ("p. " if len(pages) == 1 else "pp. ") + ", ".join(str(p) for p in pages)


# --------------------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------------------


def sidebar(settings: Settings) -> tuple[bool, bool]:
    with st.sidebar:
        st.header("Settings")
        can_ocr = ocr_available()
        enable_ocr = st.toggle(
            "OCR for scanned / image-only pages",
            value=settings.enable_ocr and can_ocr,
            disabled=not can_ocr,
            help=None if can_ocr else "Install rapidocr-onnxruntime to enable OCR.",
        )
        ten_footer = st.toggle("TEN Capital footer", value=settings.ten_footer)
        with st.expander("Configuration", icon=":material/tune:"):
            for label, value in settings.describe().items():
                st.markdown(f"**{label}:** {value}")
            st.caption("The uploaded deck is the only factual source; no web lookups are made.")
            if settings.sends_externally and st.button("Test API connection", width="stretch",
                                                       help="Checks the key and model. No tokens are used."):
                try:
                    st.success(check_connection(settings))
                except TeaserError as exc:
                    st.error(str(exc))
        if st.button("Start over", width="stretch"):
            _reset()
            st.rerun()
    return enable_ocr, ten_footer


# --------------------------------------------------------------------------------------
# Step 1: upload and ingest
# --------------------------------------------------------------------------------------


def upload_step(enable_ocr: bool) -> None:
    upload = st.file_uploader("Choose or drop a pitch deck (PDF, PPTX, or PPT)", type=["pdf", "pptx", "ppt"])
    if st.button("Use synthetic sample deck", type="tertiary", icon=":material/science:",
                 help="A fictional biotech deck seeded with confidential content to demonstrate screening."):
        _reset()
        st.session_state.sample = True
        _ingest(SAMPLE_DECK.read_bytes(), SAMPLE_DECK.name, enable_ocr)
        return

    if upload is None:
        return
    data = upload.getvalue()
    key = hashlib.sha256(data).hexdigest()
    if st.session_state.get("deck_key") == key and "deck" in st.session_state:
        return
    if st.session_state.get("deck_key") != key:
        _reset()
    st.session_state.deck_key = key
    password = None
    if st.session_state.get("password_needed"):
        password = st.text_input("This PDF is password-protected. Password:", type="password")
        if not password:
            return
    _ingest(data, upload.name, enable_ocr, password)


def _ingest(data: bytes, name: str, enable_ocr: bool, password: str | None = None) -> None:
    with st.spinner("Reading deck…"):
        try:
            deck = load_deck(data, name, password=password, enable_ocr=enable_ocr)
        except PasswordRequiredError as exc:
            st.session_state.password_needed = True
            st.warning(str(exc))
            if password is None:
                st.rerun()
            return
        except TeaserError as exc:
            st.error(str(exc))
            return
        except Exception:  # never echo deck content in an unexpected traceback
            st.error("The deck could not be read. Try exporting it again as PDF.")
            return
    st.session_state.pop("password_needed", None)
    st.session_state.deck = deck
    st.session_state.pop("draft", None)


def deck_summary() -> None:
    deck = st.session_state.deck
    readable = deck.page_count - len(deck.unreadable_pages)
    st.success(f"Read **{deck.filename}**: {deck.page_count} pages, {readable} with readable text.")
    for warning in deck.warnings:
        st.warning(warning)
    with st.expander("Extracted text by page (what the draft is based on)"):
        for page in deck.pages:
            tag = {"ocr": " · OCR", "mixed": " · partly OCR", "none": " · no text"}.get(page.text_source, "")
            st.markdown(f"**Page {page.index}{tag}** — {page.title or ''}")
            st.text(page.text or "[no readable text]")


# --------------------------------------------------------------------------------------
# Step 2: generate
# --------------------------------------------------------------------------------------


def generate_step(settings: Settings, ten_footer: bool) -> None:
    options = {}
    if settings.provider != "offline":
        options[f"Model: {settings.provider_label} ({settings.model})"] = "configured"
    options["Offline extractive (no model; verbatim deck lines)"] = "offline"
    if st.session_state.get("sample"):
        options["Recorded sample output (fixture; no model call)"] = "fixture"
    choice = options[st.radio("Drafting method", list(options))]

    consent = True
    if choice == "configured":
        consent = st.checkbox(
            f"I am permitted to send this deck's content to the {settings.provider_label} for processing."
        )
    if st.button("Generate one-pager draft", type="primary", disabled=not consent, width="stretch"):
        try:
            if choice == "configured":
                provider = make_provider(settings)
            elif choice == "fixture":
                provider = FixtureProvider()
            else:
                provider = OfflineExtractor()
            with st.spinner("Drafting and screening…"):
                st.session_state.draft = generate_draft(st.session_state.deck, provider)
            deck = st.session_state.deck
            st.session_state.run_key = uuid.uuid4().hex
            st.session_state.run_info = mailer.RunInfo(
                deck_filename=deck.filename, page_count=deck.page_count, method=provider.label,
                timestamp=dt.datetime.now(dt.UTC),
            )
            _email_result("draft", settings, ten_footer)
        except TeaserError as exc:
            st.error(str(exc))
        except Exception:
            st.error("Draft generation failed unexpectedly. No partial draft was kept.")


# --------------------------------------------------------------------------------------
# Step 3: edit
# --------------------------------------------------------------------------------------


def _refs_caption(item: TeaserItem) -> str:
    if not item.refs:
        return "Source: none"
    parts = []
    for ref in item.refs:
        mark = "✓" if ref.verified else "?"
        parts.append(f"p.{ref.page} {mark}")
    return "Source: " + " · ".join(dict.fromkeys(parts))


def _flag_widgets(draft: TeaserDraft, item: TeaserItem) -> None:
    for flag in draft.flags_for(item.id):
        if flag.severity is Severity.BLOCKING:
            flag.approved = _bound_checkbox(f"⛔ {flag.message}  — **approve**", f"approve-{flag.id}", flag.approved)
        elif flag.severity is Severity.WARNING:
            st.warning(flag.message, icon="⚠️")
        else:
            st.caption(f"ℹ️ {flag.message}")


def _item_editor(draft: TeaserDraft, item: TeaserItem) -> None:
    deck = st.session_state.deck
    with st.container(border=True):
        top = st.columns([0.6, 9, 0.8], vertical_alignment="top")
        with top[0]:
            item.include = _bound_checkbox("Use", f"inc-{item.id}", item.include,
                                           label_visibility="collapsed", help="Include in the teaser")
        # Size the box to the text (about 62 characters per line) so nothing is hidden.
        lines = max(2, -(-len(item.text) // 62))
        text = top[1].text_area("Text", value=item.text, key=f"text-{item.id}", height=min(260, 24 * lines + 22),
                                label_visibility="collapsed")
        if top[2].button("✕", key=f"del-{item.id}", help="Delete this item"):
            remove_item(draft, item.id)
            st.rerun()
        if " ".join(text.split()) != item.text:
            item.text = " ".join(text.split())
            recheck_item(draft, item, deck)
        st.caption(f"{_refs_caption(item)} · {item.claim_type.value}" + (" · added by you" if item.user_added else ""))
        if item.refs:
            with st.popover("Show source quotes"):
                for ref in item.refs:
                    st.markdown(f"**p.{ref.page}** {'✓ found' if ref.verified else '? not found on page'}")
                    if ref.quote:
                        st.text(ref.quote)
        _flag_widgets(draft, item)


def edit_step(draft: TeaserDraft) -> None:
    ui.section_title("Review and edit", "Edit any text; every change is re-checked against the cited pages.")
    st.caption(f"Drafted with: {draft.generated_by}. ✓ = quote verified on the cited page; ? = not found.")

    with st.container(border=True):
        st.markdown("**Header**")
        for label, field, required in (("Company name", draft.company_name, True),
                                        ("Tagline", draft.tagline, False),
                                        ("Website", draft.website, False)):
            cols = st.columns([0.6, 9.4], vertical_alignment="bottom")
            if not required:
                with cols[0]:
                    field.include = _bound_checkbox("Use", f"hdr-inc-{label}", field.include,
                                                    label_visibility="collapsed", help=f"Include the {label.lower()}")
            field.value = cols[1].text_input(label, value=field.value, key=f"hdr-{label}").strip()
            if not field.value:
                st.caption(f"⚠️ Not found in the deck — {'required' if required else 'will be omitted'}.")
            elif field.refs:
                st.caption(f"Source: {_pages(sorted({r.page for r in field.refs}))}")

    for key in SectionKey:
        items = draft.section_items(key)
        with st.container(border=True):
            draft.headings[key] = st.text_input(
                "Section heading", value=draft.headings.get(key, key.value), key=f"head-{key.value}"
            )
            if not items:
                st.caption(f"⚠️ Not found in the deck: {SECTION_PURPOSE[key]}. Section will be omitted.")
            visible = [i for i in items if i.include or not _sensitive(draft, i)]
            hidden = [i for i in items if not i.include and _sensitive(draft, i)]
            for item in visible:
                _item_editor(draft, item)
            if hidden:
                with st.expander(f"Excluded by default for confidentiality ({len(hidden)})"):
                    st.caption("Open only if you need to review or reinstate them. Reinstating requires approval.")
                    for item in hidden:
                        cats = sorted({f.category for f in draft.flags_for(item.id)
                                       if f.category in EXCLUDE_BY_DEFAULT}, key=lambda c: c.value)
                        st.markdown(f"*{', '.join(SENSITIVE_LABELS[c] for c in cats)}* ({_pages(item.pages)})")
                        _item_editor(draft, item)
            if st.button("Add item", key=f"add-{key.value}"):
                add_item(draft, key, st.session_state.deck, "")
                st.rerun()


def _sensitive(draft: TeaserDraft, item: TeaserItem) -> bool:
    return any(f.category in EXCLUDE_BY_DEFAULT for f in draft.flags_for(item.id))


# --------------------------------------------------------------------------------------
# Review panel
# --------------------------------------------------------------------------------------


def review_panel(draft: TeaserDraft) -> list[str]:
    ui.section_title("Review panel", "Confidentiality, accuracy, and missing information.")
    st.info(
        "Automated screening is a first pass. It can miss sensitive information and can flag "
        "harmless text. A person must read the final teaser and confirm it is cleared for "
        "distribution."
    )
    for flag in [f for f in draft.flags if f.item_id is None and f.severity is Severity.BLOCKING]:
        flag.approved = _bound_checkbox(f"⛔ {flag.message} ({_pages(flag.pages)})",
                                        f"deck-approve-{flag.id}", flag.approved)
    if draft.confidential_pages:
        st.button(f"Approve all content drawn from confidential pages ({_pages(draft.confidential_pages)})",
                  on_click=_approve_confidential_callback)

    blockers = export_blockers(draft)
    words = word_count(draft)
    c1, c2 = st.columns(2)
    c1.metric("Open approvals", len(blockers))
    c2.metric("Words", words, help=f"Target {WORD_TARGET[0]}–{WORD_TARGET[1]}")
    if words < WORD_TARGET[0]:
        st.caption(f"Below the {WORD_TARGET[0]}-word target; fine if the deck supports no more.")
    elif words > WORD_TARGET[1]:
        st.caption(f"Above the {WORD_TARGET[1]}-word target; consider tightening.")

    # Confidentiality: categories and pages only; no sensitive text is reproduced here.
    st.markdown("**Confidentiality screening**")
    rows = []
    for cat in SensitiveCategory:
        page_flags = [f for f in draft.flags if f.item_id is None and f.category is cat]
        item_flags = [f for f in draft.flags if f.item_id and f.category is cat]
        if not page_flags and not item_flags:
            continue
        pages = sorted({p for f in page_flags + item_flags for p in f.pages})
        items = {f.item_id for f in item_flags}
        included = [i for i in (draft.item(x) for x in items) if i and i.include]
        pending = [f for f in item_flags if not f.approved and _included(draft, f.item_id)]
        if not items:
            status = "Found in deck; not used in draft"
        elif pending:
            status = f"{len(pending)} awaiting approval"
        elif included:
            status = "Approved for use"
        else:
            status = "Excluded"
        rows.append({"Category": SENSITIVE_LABELS[cat], "Pages": _pages(pages), "Items": len(items), "Status": status})
    if rows:
        st.table(rows)  # static and wrapping: category names stay fully readable in a narrow column
    else:
        st.caption("No confidentiality categories detected.")

    st.markdown("**Accuracy and source checks**")
    accuracy = [f for f in draft.flags if f.kind in (FlagKind.UNSUPPORTED, FlagKind.FIGURE_MISMATCH, FlagKind.QUALIFIER_DRIFT)
                and _included(draft, f.item_id)]
    if not accuracy:
        st.caption("All included claims cite verified source text.")
    for f in accuracy:
        status = "approved" if f.approved else f.severity.value
        st.markdown(f"- `{status}` {f.message}")
    for f in draft.flags:
        if f.item_id is None and f.kind in (FlagKind.CONFLICT, FlagKind.UNCLEAR, FlagKind.EMBEDDED_INSTRUCTION, FlagKind.EXTRACTION):
            st.warning(f"{f.message} ({_pages(f.pages)})")

    missing = missing_sections(draft)
    header_missing = [n for n, f in (("tagline", draft.tagline), ("website", draft.website)) if not f.value]
    if missing or header_missing:
        st.markdown("**Missing from the deck**")
        for n in header_missing:
            st.markdown(f"- {n.capitalize()}")
        for k in missing:
            st.markdown(f"- {draft.headings.get(k, k.value)} ({SECTION_PURPOSE[k]})")

    if blockers:
        with st.expander(f"Items needing approval before export ({len(blockers)})", expanded=False):
            for reason in blockers:
                st.markdown(f"- {reason}")
    return blockers


# --------------------------------------------------------------------------------------
# Step 4: preview and export
# --------------------------------------------------------------------------------------


def _to_png(data: bytes) -> bytes | None:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return None
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _email_skip_reason(settings: Settings) -> str | None:
    if not settings.email_enabled:
        return "Result emails are off (RESEND_API_KEY is not set)."
    if st.session_state.get("sample") and not settings.email_demo_runs:
        return "Synthetic sample runs are not emailed (set EMAIL_DEMO_RUNS=true to include them)."
    return None


def _email_result(event: mailer.Event, settings: Settings, ten_footer: bool,
                  pdf: bytes | None = None, docx: bytes | None = None) -> None:
    """Email a draft or final result. Never raises; records the outcome for display."""
    skip = _email_skip_reason(settings)
    statuses = st.session_state.setdefault("email_status", {})
    if skip:
        statuses[event] = (False, skip)
        return
    draft: TeaserDraft = st.session_state.draft
    problem = None
    if pdf is None:
        try:
            pdf = render_pdf(draft, RenderOptions(ten_footer=ten_footer)).pdf
        except TeaserError as exc:
            problem = str(exc)
    payload = mailer.build_payload(event, draft, st.session_state.run_info, settings,
                                   pdf=pdf, docx=docx, pdf_problem=problem)
    key = mailer.idempotency_key(event, st.session_state.run_key, pdf)
    if key in st.session_state.setdefault("emailed", set()):
        return
    outcome = mailer.send(payload, settings, key)
    if outcome.sent:
        st.session_state.emailed.add(key)
    label = "Draft analysis" if event == "draft" else "Approved teaser"
    statuses[event] = (outcome.sent, f"{label}: {outcome.detail}")


def _email_final(pdf: bytes, docx: bytes | None) -> None:
    settings = load_settings()
    _email_result("final", settings, st.session_state.get("ten_footer", True), pdf=pdf, docx=docx)


def _email_status(event: mailer.Event) -> None:
    status = st.session_state.get("email_status", {}).get(event)
    if status:
        ok, text = status
        (st.caption if ok or "off" in text or "not emailed" in text else st.warning)(("✉️ " if ok else "") + text)


def export_step(draft: TeaserDraft, blockers: list[str], ten_footer: bool) -> None:
    ui.section_title("Preview and export", "One page, US Letter. Export unlocks once every flagged item is approved or excluded.")
    deck = st.session_state.deck
    b1, b2, b3 = st.columns([1, 1, 2])
    draft.brand.primary = b1.color_picker("Heading colour", value=draft.brand.primary)
    draft.brand.accent = b2.color_picker("Accent colour", value=draft.brand.accent)
    b3.caption(f"Colours: {draft.brand.source}" + ("" if draft.brand.detected else " (no brand colour reliably identified)"))

    include_logo = st.checkbox("Include a logo", value=False,
                               help="Only include a logo you are permitted to reuse.")
    if include_logo:
        candidates = [png for png in (_to_png(b) for b in deck.logo_candidates) if png]
        uploaded = st.file_uploader("Upload a logo (PNG/JPG)", type=["png", "jpg", "jpeg"], key="logo-upload")
        if uploaded is not None:
            draft.brand.logo_png = _to_png(uploaded.getvalue())
        elif candidates:
            st.caption("Images found on the cover page:")
            cols = st.columns(min(4, len(candidates)))
            choice = st.radio("Logo", list(range(len(candidates))), horizontal=True,
                              format_func=lambda i: f"Image {i + 1}")
            for col, png in zip(cols, candidates, strict=False):
                col.image(png, width=120)
            draft.brand.logo_png = candidates[choice]
        else:
            st.caption("No logo image was found in the deck; upload one to include it.")
    options = RenderOptions(ten_footer=ten_footer, include_logo=include_logo and draft.brand.logo_png is not None)

    try:
        result = render_pdf(draft, options)
    except LayoutOverflowError as exc:
        st.error(str(exc))
        return
    except TeaserError as exc:
        st.error(str(exc))
        return

    report = result.report
    issues = layout_audit(result.pdf)
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.image(preview_png(result.pdf, dpi=110), width="stretch")
    with right:
        _export_panel(draft, blockers, issues, report, options)


EXPORT_FORMATS = {
    "PDF (distribution copy)": "pdf",
    "Word (editable DOCX)": "docx",
    "PNG image": "png",
    "All files (ZIP: PDF, DOCX, PNG, review record)": "zip",
}


def _export_panel(draft: TeaserDraft, blockers: list[str], issues: list[str], report: object,
                  options: RenderOptions) -> None:
    """Readiness checklist, then one button that submits the approval and downloads."""
    st.markdown("**Ready to export?**")
    checks = [
        (not blockers, "All flagged items approved or excluded" if not blockers
         else f"{len(blockers)} flagged item(s) still need approval or exclusion (see the review panel)"),
        (True, f"Fits on one US Letter page at {report.body_size:g} pt ({report.words} words)"),  # type: ignore[attr-defined]
        (not issues, "Layout check passed: nothing clipped or overlapping" if not issues
         else "Layout check found problems: " + "; ".join(issues)),
    ]
    for ok, text in checks:
        st.markdown(f"{'✅' if ok else '⛔'} {text}")

    ready = not blockers and not issues
    choice = EXPORT_FORMATS[st.radio("Format", list(EXPORT_FORMATS), disabled=not ready)]
    if ready:
        source = export.SourceInfo(
            deck_filename=st.session_state.deck.filename, page_count=st.session_state.deck.page_count,
            unreadable_pages=st.session_state.deck.unreadable_pages, generated_by=draft.generated_by,
            generated_at=getattr(st.session_state.get("run_info"), "timestamp", None),
        )
        bundle = export.build_bundle(draft, source, options)
        files = bundle.files()
        if choice == "zip":
            data, name, mime = bundle.zip(), f"{bundle.stem}.zip", "application/zip"
        else:
            name = f"{bundle.stem}.{choice}"
            data, mime = files[name]
        st.download_button(
            "Submit approval & download", data, file_name=name, mime=mime, type="primary",
            width="stretch", icon=":material/verified:", on_click=_submit_approval,
            args=(bundle.pdf, bundle.docx, name),
            help="Confirms you have read the full teaser and approve it for distribution, then downloads it.",
        )
        st.caption("By submitting, you confirm you have read the full teaser and approve it for distribution.")
    else:
        st.button("Submit approval & download", type="primary", width="stretch", disabled=True,
                  icon=":material/verified:", key="submit-disabled")
    approved = st.session_state.get("approval")
    if approved:
        st.success(f"Approved and downloaded {approved['file']} at {approved['at']:%H:%M} UTC.")
    _email_status("final")

    with st.expander("Internal draft (before approval)", icon=":material/draft:"):
        st.caption("A watermarked PDF of the teaser as it stands, including items not yet approved. "
                   "For internal review only; not for distribution.")
        st.download_button("Download DRAFT PDF", export.draft_pdf(draft, options),
                           file_name=f"{export.file_stem(draft, draft_copy=True)}.pdf", mime="application/pdf",
                           width="stretch", icon=":material/download:")


def _submit_approval(pdf: bytes, docx: bytes, filename: str) -> None:
    """Runs when the reviewer clicks Submit approval & download: record it and email the result."""
    st.session_state.approval = {"file": filename, "at": dt.datetime.now(dt.UTC)}
    _email_final(pdf, docx)


# --------------------------------------------------------------------------------------


def _current_step() -> int:
    """0 Upload, 1 Draft, 2 Review, 3 Export: drives the step strip in the hero card."""
    if "deck" not in st.session_state:
        return 0
    draft: TeaserDraft | None = st.session_state.get("draft")
    if draft is None:
        return 1
    return 3 if not export_blockers(draft) else 2


def main() -> None:
    ui.inject_css()
    settings = _settings()
    _, center, _ = st.columns([1, 2.4, 1])
    with center:
        ui.brand()
        if settings is None:
            return
    enable_ocr, ten_footer = sidebar(settings)
    st.session_state.ten_footer = ten_footer

    with center, st.container(key="hero"):
        ui.hero_intro(_current_step())
        upload_step(enable_ocr)
        if "deck" in st.session_state:
            deck_summary()
            generate_step(settings, ten_footer)
            if "draft" in st.session_state:
                _email_status("draft")
        ui.disclosure(settings)

    draft: TeaserDraft | None = st.session_state.get("draft")
    if draft is not None:
        st.write("")
        edit_col, panel_col = st.columns([3, 2], gap="large")
        with edit_col:
            edit_step(draft)
        with panel_col:
            blockers = review_panel(draft)
        st.write("")
        export_step(draft, blockers, ten_footer)
    ui.footer()


main()
