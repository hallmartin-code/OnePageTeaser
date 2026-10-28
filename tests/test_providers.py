"""Configuration and provider adapters, exercised with fake clients (no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from teaser.config import load_settings
from teaser.errors import ConfigError, ExtractionError
from teaser.extract.providers import AnthropicProvider, OpenAICompatibleProvider, make_provider
from teaser.schema import LLMDraft

from .conftest import make_deck

DECK = make_deck("Acme\nRobots", "Market\nTAM: $4.2B")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    for var in ("TEASER_LLM_PROVIDER", "TEASER_LLM_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "TEASER_LLM_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path, **env: str):  # type: ignore[no-untyped-def]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return load_settings(env_file=tmp_path / "missing.env")


def test_missing_key_is_a_clear_config_error(clean_env, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, tmp_path, TEASER_LLM_PROVIDER="anthropic")
    assert settings.model == "claude-opus-5-5"
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        make_provider(settings)


def test_describe_never_reveals_the_key(clean_env, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="sk-ant-secret-value")
    assert "sk-ant-secret-value" not in json.dumps(settings.describe())
    assert settings.describe()["API key"] == "configured"


def test_offline_needs_no_key_and_sends_nothing(clean_env, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, tmp_path, TEASER_LLM_PROVIDER="offline")
    assert not settings.sends_externally
    assert make_provider(settings).extract(DECK).company_name.value == "Acme"


def test_unknown_provider(clean_env, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError, match="not supported"):
        _settings(monkeypatch, tmp_path, TEASER_LLM_PROVIDER="mystery")


class _FakeAnthropicMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def _anthropic(monkeypatch, tmp_path, response):  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="sk-test")
    provider = AnthropicProvider(settings)
    fake = _FakeAnthropicMessages(response)
    provider._client = SimpleNamespace(beta=SimpleNamespace(messages=fake))  # type: ignore[assignment]
    return provider, fake


def test_anthropic_request_shape(clean_env, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    draft = LLMDraft()
    provider, fake = _anthropic(monkeypatch, tmp_path, SimpleNamespace(stop_reason="end_turn", parsed_output=draft))
    assert provider.extract(DECK) is draft
    assert fake.kwargs["model"] == "claude-opus-5-5"
    assert fake.kwargs["output_format"] is LLMDraft
    assert fake.kwargs["output_config"] == {"effort": "high"}
    assert fake.kwargs["fallbacks"] == "default"
    content = fake.kwargs["messages"][0]["content"]
    assert '<page number="2">' in content and "TAM: $4.2B" in content
    assert "untrusted" in fake.kwargs["system"]


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_anthropic_unusable_stop_reasons(clean_env, monkeypatch, tmp_path, stop) -> None:  # type: ignore[no-untyped-def]
    provider, _ = _anthropic(monkeypatch, tmp_path, SimpleNamespace(stop_reason=stop, parsed_output=None))
    with pytest.raises(ExtractionError):
        provider.extract(DECK)


def test_openai_compatible_retries_invalid_json(clean_env, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(monkeypatch, tmp_path, TEASER_LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test",
                         TEASER_LLM_MODEL="some-model")
    provider = OpenAICompatibleProvider(settings)
    replies = iter(['{"items": "not a list"}', LLMDraft().model_dump_json()])
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        msg = SimpleNamespace(content=next(replies))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # type: ignore[assignment]
    assert isinstance(provider.extract(DECK), LLMDraft)
    assert len(calls) == 2 and calls[0]["response_format"] == {"type": "json_object"}


def test_hosted_without_password_is_blocked(clean_env, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    settings = _settings(monkeypatch, tmp_path, RAILWAY_ENVIRONMENT="production")
    assert settings.hosted and settings.access_blocked
    settings = _settings(monkeypatch, tmp_path, RAILWAY_ENVIRONMENT="production", APP_PASSWORD="s3cret")
    assert not settings.access_blocked
    assert "s3cret" not in json.dumps(settings.describe())


def test_check_connection_uses_model_lookup_not_messages(clean_env, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import anthropic

    from teaser.extract.providers import check_connection

    seen: list[str] = []

    class FakeModels:
        def retrieve(self, model_id: str) -> Any:
            seen.append(model_id)
            return SimpleNamespace(id=model_id, display_name="Claude Opus 5.5")

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="sk-test")
    assert "Claude Opus 5.5" in check_connection(settings)
    assert seen == ["claude-opus-5-5"]
