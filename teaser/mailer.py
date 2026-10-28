"""Email each analysis result through Resend.

Two events are mailed to ``REPORT_EMAIL_TO`` (default Info@tencapital.group):

* ``draft``: a draft was generated. The email summarises the analysis and attaches the
  draft PDF as it stands, marked DRAFT, with open approvals listed.
* ``final``: a reviewer downloaded the approved teaser. The email attaches the final PDF
  and DOCX.

Rules this module keeps:

* **A failed send never fails the app.** Every error becomes an ``EmailOutcome``; nothing
  here raises.
* **No excluded content leaves the app.** Bodies describe confidentiality findings by
  category and page only. Attachments are rendered from included items only, exactly as
  the PDF would be.
* **No duplicates.** Each send carries a Resend ``Idempotency-Key`` derived from the event
  and content, so Streamlit reruns cannot send the same email twice.

Resend's API is one JSON POST, sent with ``urllib`` (as in the other TEN Capital tools).
Resend sits behind Cloudflare, which rejects urllib's default User-Agent with error 1010,
so the request identifies itself explicitly.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import escape
from typing import Any, Literal

from teaser.config import Settings
from teaser.editing import export_blockers, missing_sections, word_count
from teaser.schema import SENSITIVE_LABELS, FlagKind, SensitiveCategory, Severity, TeaserDraft

RESEND_ENDPOINT = "https://api.resend.com/emails"
USER_AGENT = "onepage-teaser/1.0 (+https://tencapital.group)"
TIMEOUT_S = 20.0
MAX_ATTACHMENT_BYTES = 35 * 1024 * 1024  # Resend caps a message at 40 MB

Event = Literal["draft", "final"]


@dataclass(frozen=True)
class EmailOutcome:
    """What happened when a result was mailed. Never an exception."""

    sent: bool
    detail: str
    message_id: str | None = None


@dataclass(frozen=True)
class RunInfo:
    """Context about the run that is not part of the draft itself."""

    deck_filename: str
    page_count: int
    method: str  # e.g. "Anthropic · claude-opus-5-5"
    timestamp: dt.datetime


# --------------------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------------------


def _company(draft: TeaserDraft, run: RunInfo) -> str:
    return draft.company_name.value.strip() or run.deck_filename.rsplit(".", 1)[0]


def subject_for(event: Event, draft: TeaserDraft, run: RunInfo) -> str:
    """Email subject line."""
    name = _company(draft, run)
    if event == "final":
        return f"Investor teaser approved: {name}"
    open_items = len(export_blockers(draft))
    return f"Investor teaser draft: {name} ({open_items} approval{'s' if open_items != 1 else ''} open)"


def _screening_rows(draft: TeaserDraft) -> list[tuple[str, str, str]]:
    """(category, pages, status) for every confidentiality category found. No content."""
    rows = []
    for cat in SensitiveCategory:
        flags = [f for f in draft.flags if f.category is cat]
        if not flags:
            continue
        pages = sorted({p for f in flags for p in f.pages})
        item_flags = [f for f in flags if f.item_id]
        included = [f for f in item_flags if (i := draft.item(f.item_id or "")) and i.include]
        if not item_flags:
            status = "In deck; not used"
        elif any(not f.approved for f in included):
            status = "Awaiting approval"
        elif included:
            status = "Approved for use"
        else:
            status = "Excluded"
        rows.append((SENSITIVE_LABELS[cat], ", ".join(map(str, pages)) or "-", status))
    return rows


def _notes(draft: TeaserDraft) -> list[str]:
    """Accuracy and data-quality notes. Only included items' text can appear here."""
    notes: list[str] = []
    for flag in draft.flags:
        if flag.item_id is None and flag.kind in (
            FlagKind.CONFLICT, FlagKind.UNCLEAR, FlagKind.EMBEDDED_INSTRUCTION, FlagKind.EXTRACTION
        ):
            pages = ""
            if flag.pages and not re.search(r"\bpp?\. \d", flag.message):
                pages = f" ({'page' if len(flag.pages) == 1 else 'pages'} {', '.join(map(str, flag.pages))})"
            notes.append(flag.message + pages)
        elif flag.kind in (FlagKind.UNSUPPORTED, FlagKind.FIGURE_MISMATCH, FlagKind.QUALIFIER_DRIFT):
            item = draft.item(flag.item_id or "")
            if item and item.include and not flag.approved and flag.severity is not Severity.INFO:
                notes.append(flag.message)
    return list(dict.fromkeys(notes))


def summary(draft: TeaserDraft, run: RunInfo) -> dict[str, Any]:
    """Everything the email body shows, as plain data (also used by the tests)."""
    present = [draft.headings[k] for k in draft.headings if draft.section_items(k, included_only=True)]
    return {
        "company": _company(draft, run),
        "tagline": draft.tagline.value if draft.tagline.include else "",
        "metrics": [
            ("Pages read", str(run.page_count)),
            ("Words", str(word_count(draft))),
            ("Items included", f"{sum(i.include for i in draft.items)} of {len(draft.items)}"),
            ("Open approvals", str(len(export_blockers(draft)))),
        ],
        "sections_present": present,
        "sections_missing": [draft.headings[k] for k in missing_sections(draft)],
        "screening": _screening_rows(draft),
        "notes": _notes(draft),
        "source": f"{run.deck_filename} · {run.page_count} pages · {run.method} · "
                  f"{run.timestamp:%Y-%m-%d %H:%M} UTC",
    }


def _list(items: list[str]) -> str:
    if not items:
        return "<p style='margin:0;color:#6b7280'>None</p>"
    return "<ul style='margin:0;padding-left:18px'>" + "".join(
        f"<li style='margin:0 0 4px'>{escape(i)}</li>" for i in items) + "</ul>"


def _heading(text: str) -> str:
    return (f"<div style=\"font-weight:600;font-size:13px;color:#1f3a5f;text-transform:uppercase;"
            f"letter-spacing:.05em;margin:18px 0 6px\">{escape(text)}</div>")


def build_html(event: Event, draft: TeaserDraft, run: RunInfo, attachment_note: str) -> str:
    """HTML body."""
    s = summary(draft, run)
    banner = (
        "Approved by the reviewer and downloaded for distribution."
        if event == "final"
        else "AI-generated draft. Not yet reviewed or approved for distribution."
    )
    metrics = "".join(
        f"<td style='padding:8px 12px;border-right:1px solid #d8dce3;vertical-align:top'>"
        f"<div style='font-size:10px;letter-spacing:.06em;color:#6b7280;text-transform:uppercase'>{escape(k)}</div>"
        f"<div style='font-size:15px;margin-top:2px'>{escape(v)}</div></td>"
        for k, v in s["metrics"]
    )
    screening = (
        "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
        "<tr style='text-align:left;color:#6b7280'><th>Category</th><th>Pages</th><th>Status</th></tr>"
        + "".join(f"<tr><td style='padding:3px 6px 3px 0'>{escape(c)}</td><td>{escape(p)}</td>"
                  f"<td>{escape(st)}</td></tr>" for c, p, st in s["screening"])
        + "</table>"
        if s["screening"]
        else "<p style='margin:0;color:#6b7280'>No confidentiality categories detected.</p>"
    )
    return f"""<div style="font:15px/1.55 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#14181f;max-width:640px">
  <div style="border-bottom:2px solid #1f3a5f;padding-bottom:10px;margin-bottom:14px">
    <div style="font-weight:700;font-size:22px">{escape(s['company'])}</div>
    <div style="color:#6b7280;font-size:14px">{escape(s['tagline'])}</div>
    <div style="color:#6b7280;font-size:12px;margin-top:6px">One-page investor teaser · {escape(banner)}</div>
  </div>
  <table style="width:100%;border-collapse:collapse;background:#eef2f8;border-radius:6px"><tr>{metrics}</tr></table>
  {_heading("Sections in the teaser")}{_list(s["sections_present"])}
  {_heading("Not found in the deck")}{_list(s["sections_missing"])}
  {_heading("Confidentiality screening")}{screening}
  {_heading("Accuracy and data-quality notes")}{_list(s["notes"])}
  <div style="font-size:12px;color:#6b7280;border-top:1px solid #d8dce3;padding-top:10px;margin-top:20px">
    {escape(attachment_note)}<br>Source: {escape(s['source'])}<br>
    Screening results list categories and pages only; excluded content is not included in this email.<br>
    Generated by TEN Capital · Internal use only.
  </div>
</div>"""


def build_text(event: Event, draft: TeaserDraft, run: RunInfo, attachment_note: str) -> str:
    """Plain-text alternative."""
    s = summary(draft, run)
    lines = [s["company"], s["tagline"], "",
             "APPROVED TEASER" if event == "final" else "DRAFT TEASER (not yet approved)", ""]
    lines += [f"  {k}: {v}" for k, v in s["metrics"]]
    lines += ["", "SECTIONS IN THE TEASER"] + [f"  - {x}" for x in s["sections_present"] or ["None"]]
    lines += ["", "NOT FOUND IN THE DECK"] + [f"  - {x}" for x in s["sections_missing"] or ["None"]]
    lines += ["", "CONFIDENTIALITY SCREENING"]
    lines += [f"  - {c} (pages {p}): {st}" for c, p, st in s["screening"]] or ["  - None detected"]
    lines += ["", "ACCURACY AND DATA-QUALITY NOTES"] + [f"  - {x}" for x in s["notes"] or ["None"]]
    lines += ["", attachment_note, f"Source: {s['source']}", "Generated by TEN Capital - internal use only."]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Payload and send
# --------------------------------------------------------------------------------------


def _attachment(filename: str, data: bytes | None) -> dict[str, str] | None:
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        return None
    return {"filename": filename, "content": base64.standard_b64encode(data).decode("ascii")}


def _safe_name(draft: TeaserDraft, run: RunInfo) -> str:
    keep = "".join(ch if ch.isalnum() else "_" for ch in _company(draft, run))
    return keep.strip("_") or "Company"


def build_payload(
    event: Event,
    draft: TeaserDraft,
    run: RunInfo,
    settings: Settings,
    *,
    pdf: bytes | None,
    docx: bytes | None = None,
    pdf_problem: str | None = None,
) -> dict[str, Any]:
    """The Resend request body. Separate from sending so it can be tested offline."""
    stem = f"{_safe_name(draft, run)}_Investor_Teaser" + ("" if event == "final" else "_DRAFT")
    attachments = [a for a in (_attachment(f"{stem}.pdf", pdf),
                               _attachment(f"{stem}.docx", docx if event == "final" else None)) if a]
    if pdf:
        note = f"Attached: {stem}.pdf" + (" and .docx" if len(attachments) > 1 else "") + "."
    else:
        note = f"No PDF attached: {pdf_problem or 'the teaser could not be rendered'}."
    return {
        "from": settings.report_email_from,
        "to": settings.email_recipients,
        "subject": subject_for(event, draft, run),
        "html": build_html(event, draft, run, note),
        "text": build_text(event, draft, run, note),
        "attachments": attachments,
        "tags": [{"name": "app", "value": "onepage_teaser"}, {"name": "event", "value": event}],
    }


def idempotency_key(event: Event, run_key: str, pdf: bytes | None) -> str:
    """Same event + same run + same PDF => same key, so reruns never resend."""
    digest = hashlib.sha256(f"{event}|{run_key}|".encode() + (pdf or b"")).hexdigest()
    return f"teaser-{event}-{digest[:40]}"


def send(payload: dict[str, Any], settings: Settings, key: str) -> EmailOutcome:
    """POST to Resend. Returns what happened; never raises; never logs the key or content."""
    if not settings.email_enabled:
        return EmailOutcome(False, "Email is off (RESEND_API_KEY is not set).")
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        RESEND_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Idempotency-Key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = _readable(exc.read().decode("utf-8", "replace")[:400])
        hint = ""
        if exc.code == 403 and "domain" in detail.lower():
            hint = " Verify the sender's domain in Resend, or change REPORT_EMAIL_FROM."
        elif exc.code == 401:
            hint = " Check RESEND_API_KEY."
        return EmailOutcome(False, f"Resend rejected the email ({exc.code}): {detail}{hint}")
    except urllib.error.URLError as exc:
        return EmailOutcome(False, f"Could not reach Resend: {exc.reason}")
    except (TimeoutError, json.JSONDecodeError, OSError) as exc:
        return EmailOutcome(False, f"Emailing the result failed: {exc}")
    return EmailOutcome(True, f"Emailed to {', '.join(settings.email_recipients)}.",
                        str(body.get("id")) if isinstance(body, dict) else None)


def _readable(body: str) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(parsed, dict):
        return str(parsed.get("message") or parsed.get("error") or body)
    return body
