"""Result emails via Resend: content safety, payload shape, idempotency, failure handling."""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import urllib.error
from typing import Any

import pytest

from teaser import mailer
from teaser.config import Settings
from teaser.render import render_pdf

from .conftest import approve_all

SENSITIVE = ["Northwind", "Bluefin", "6.5M", "38M", "maya.okafor", "555-0142", "63/482,117",
             "CC(C)Cc1", "Midwest GI", "top-10 pharma"]

RUN = mailer.RunInfo("sample_deck.pdf", 12, "Recorded sample output", dt.datetime(2026, 10, 28, 12, 0))


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(provider="offline", model="extractive", api_key=None, base_url=None, effort="high",
                                timeout_s=30, enable_ocr=False, ten_footer=True, resend_api_key="re_test")
    base.update(overrides)
    return Settings(**base)


def test_defaults_send_to_info_mailbox() -> None:
    settings = _settings()
    assert settings.email_recipients == ["Info@tencapital.group"]
    assert settings.report_email_from.endswith("@tencapital.group>")
    assert settings.email_enabled
    assert not _settings(resend_api_key=None).email_enabled
    assert "re_test" not in json.dumps(settings.describe())


def test_draft_payload_shape_and_no_sensitive_text(sample_draft) -> None:  # type: ignore[no-untyped-def]
    pdf = render_pdf(sample_draft).pdf
    payload = mailer.build_payload("draft", sample_draft, RUN, _settings(), pdf=pdf)
    assert payload["to"] == ["Info@tencapital.group"]
    assert payload["subject"].startswith("Investor teaser draft: Halcyon Therapeutics (")
    assert [a["filename"] for a in payload["attachments"]] == ["Halcyon_Therapeutics_Investor_Teaser_DRAFT.pdf"]
    assert base64.b64decode(payload["attachments"][0]["content"]) == pdf
    body = payload["html"] + payload["text"]
    for s in SENSITIVE:
        assert s not in body
    assert "Confidentiality screening" in payload["html"] and "Investor names / identities" in body
    assert "not yet reviewed or approved" in payload["html"].lower()


def test_final_payload_attaches_pdf_and_docx(sample_draft) -> None:  # type: ignore[no-untyped-def]
    approve_all(sample_draft)
    payload = mailer.build_payload("final", sample_draft, RUN, _settings(), pdf=b"%PDF-final", docx=b"PK-docx")
    assert payload["subject"] == "Investor teaser approved: Halcyon Therapeutics"
    assert [a["filename"] for a in payload["attachments"]] == [
        "Halcyon_Therapeutics_Investor_Teaser.pdf", "Halcyon_Therapeutics_Investor_Teaser.docx"]
    assert "Open approvals" in payload["text"] and "Open approvals: 0" in payload["text"]


def test_unrenderable_draft_explains_missing_pdf(sample_draft) -> None:  # type: ignore[no-untyped-def]
    payload = mailer.build_payload("draft", sample_draft, RUN, _settings(), pdf=None, pdf_problem="content exceeds one page")
    assert payload["attachments"] == []
    assert "No PDF attached: content exceeds one page" in payload["text"]


def test_idempotency_key_is_stable_per_event_and_content() -> None:
    a = mailer.idempotency_key("draft", "run1", b"pdf")
    assert a == mailer.idempotency_key("draft", "run1", b"pdf")
    assert a != mailer.idempotency_key("final", "run1", b"pdf")
    assert a != mailer.idempotency_key("draft", "run1", b"pdf2")
    assert len(a) <= 256


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_send_success_sets_headers(monkeypatch: pytest.MonkeyPatch, sample_draft) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        return _Response(b'{"id": "email_123"}')

    monkeypatch.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    payload = mailer.build_payload("draft", sample_draft, RUN, _settings(), pdf=None)
    outcome = mailer.send(payload, _settings(), "teaser-draft-abc")
    assert outcome.sent and outcome.message_id == "email_123"
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["authorization"] == "Bearer re_test"
    assert headers["idempotency-key"] == "teaser-draft-abc"
    assert headers["user-agent"].startswith("onepage-teaser/")
    assert captured["body"]["to"] == ["Info@tencapital.group"]


def test_send_failures_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def rejected(request: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},  # type: ignore[arg-type]
                                     io.BytesIO(b'{"message": "The tencapital.group domain is not verified."}'))

    monkeypatch.setattr(mailer.urllib.request, "urlopen", rejected)
    outcome = mailer.send({"to": []}, _settings(), "k")
    assert not outcome.sent and "domain is not verified" in outcome.detail and "REPORT_EMAIL_FROM" in outcome.detail

    def offline(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(mailer.urllib.request, "urlopen", offline)
    assert not mailer.send({"to": []}, _settings(), "k").sent
    assert "off" in mailer.send({}, _settings(resend_api_key=None), "k").detail
