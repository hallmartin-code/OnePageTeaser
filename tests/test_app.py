"""End-to-end through the Streamlit script, headless (streamlit.testing.v1.AppTest)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("TEASER_LLM_PROVIDER", "offline")
    monkeypatch.setenv("TEASER_ENABLE_OCR", "false")
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def _button(at: AppTest, label: str):  # type: ignore[no-untyped-def]
    return next(b for b in at.button if b.label.startswith(label))


def _downloads(at: AppTest) -> dict[str, bool]:
    """Download buttons by label -> enabled. AppTest exposes them as UnknownElement protos."""
    return {el.proto.label: not el.proto.disabled for el in at.get("download_button")}


def _submit_enabled(at: AppTest) -> bool:
    """The single approval button is a disabled st.button until export is allowed."""
    if _downloads(at).get("Submit approval & download"):
        return True
    assert any(b.label == "Submit approval & download" and b.disabled for b in at.button)
    return False


def test_sample_deck_full_workflow(app: AppTest) -> None:
    assert not app.exception
    _button(app, "Use synthetic sample deck").click().run()
    assert any("Read **sample_deck.pdf**" in s.value for s in app.success)

    app.radio[0].set_value("Recorded sample output (fixture; no model call)").run()
    _button(app, "Generate one-pager draft").click().run()
    assert not app.exception
    assert app.session_state.draft is not None

    # Export is locked while flagged items are open; the watermarked draft is always available.
    assert not _submit_enabled(app)
    assert _downloads(app)["Download DRAFT PDF"]
    approvals = [c for c in app.checkbox if "approve" in c.label.lower()]
    assert approvals
    _button(app, "Approve all content drawn from confidential pages").click().run()
    assert not _submit_enabled(app)  # the funding ask still needs its own approval
    for box in [c for c in app.checkbox if c.label.startswith("⛔") and "Funding ask" in c.label]:
        box.check()
    app.run()
    assert not app.exception
    assert _submit_enabled(app)  # one button now submits the approval and downloads
    assert any("✅ All flagged items approved" in m.value for m in app.markdown)

    # Format choice changes what the button downloads.
    fmt = next(r for r in app.radio if r.label == "Format")
    fmt.set_value("All files (ZIP: PDF, DOCX, PNG, review record)").run()
    assert _submit_enabled(app)


def test_offline_mode_generates_and_shows_review_panel(app: AppTest) -> None:
    _button(app, "Use synthetic sample deck").click().run()
    _button(app, "Generate one-pager draft").click().run()
    assert not app.exception
    texts = " ".join(m.value for m in app.markdown)
    assert "Confidentiality screening" in texts
    assert any("instructions to an AI system" in w.value for w in app.warning)


def _sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict]:  # type: ignore[type-arg]
    from teaser import mailer

    sent: list[dict] = []  # type: ignore[type-arg]

    def fake_send(payload, settings, key):  # type: ignore[no-untyped-def]
        sent.append({"subject": payload["subject"], "key": key, "files": [a["filename"] for a in payload["attachments"]]})
        return mailer.EmailOutcome(True, "Emailed to Info@tencapital.group.", "email_1")

    monkeypatch.setattr(mailer, "send", fake_send)
    return sent


def test_sample_runs_are_not_emailed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEASER_LLM_PROVIDER", "offline")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.delenv("EMAIL_DEMO_RUNS", raising=False)
    sent = _sent_emails(monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _button(at, "Use synthetic sample deck").click().run()
    _button(at, "Generate one-pager draft").click().run()
    assert sent == []
    assert any("not emailed" in c.value for c in at.caption)


def test_draft_is_emailed_once_per_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEASER_LLM_PROVIDER", "offline")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("EMAIL_DEMO_RUNS", "true")
    sent = _sent_emails(monkeypatch)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    _button(at, "Use synthetic sample deck").click().run()
    _button(at, "Generate one-pager draft").click().run()
    at.run()  # an ordinary rerun must not send again
    assert len(sent) == 1
    assert sent[0]["subject"].startswith("Investor teaser draft: Halcyon Therapeutics")
    assert sent[0]["files"] == ["Halcyon_Therapeutics_Investor_Teaser_DRAFT.pdf"]
    assert any("Emailed to Info@tencapital.group" in c.value for c in at.caption)
