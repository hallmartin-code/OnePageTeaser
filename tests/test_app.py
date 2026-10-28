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


def _download_disabled(at: AppTest) -> bool:
    # AppTest exposes download buttons as UnknownElement; read the proto's disabled flag.
    for el in at.get("download_button"):
        if "PDF" in el.proto.label:
            return bool(el.proto.disabled)
    raise AssertionError("no PDF download button rendered")


def test_sample_deck_full_workflow(app: AppTest) -> None:
    assert not app.exception
    _button(app, "Use synthetic sample deck").click().run()
    assert any("Read **sample_deck.pdf**" in s.value for s in app.success)

    app.radio[0].set_value("Recorded sample output (fixture; no model call)").run()
    _button(app, "Generate one-pager draft").click().run()
    assert not app.exception
    assert app.session_state.draft is not None

    # Download stays disabled until approvals and the final confirmation are given.
    assert _download_disabled(app)
    approvals = [c for c in app.checkbox if "approve" in c.label.lower()]
    assert approvals
    _button(app, "Approve all content drawn from confidential pages").click().run()
    for box in [c for c in app.checkbox if c.label.startswith("⛔") and "Funding ask" in c.label]:
        box.check()
    app.run()
    assert _download_disabled(app)  # final human confirmation still missing
    next(c for c in app.checkbox if c.label.startswith("I have read the full teaser")).check().run()
    assert not app.exception
    assert not _download_disabled(app)


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
