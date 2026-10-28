"""LLM providers. Each turns a ``Deck`` into a validated ``LLMDraft``.

Provider, model, and credentials come from ``Settings`` (environment variables). Nothing
here logs prompts, responses, or keys.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from teaser.config import Settings
from teaser.errors import ConfigError, ExtractionError
from teaser.extract.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
    json_schema_instruction,
)
from teaser.ingest.models import Deck
from teaser.schema import LLMDraft

#: Models that accept the server-side refusal fallback in its "default" form.
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")
MAX_OUTPUT_TOKENS = 16000


class Provider(Protocol):
    """Anything that can draft teaser content from a deck."""

    label: str

    def extract(self, deck: Deck) -> LLMDraft: ...


class AnthropicProvider:
    """Claude via the official Anthropic SDK, with schema-constrained output."""

    def __init__(self, settings: Settings) -> None:
        import anthropic

        self._anthropic = anthropic
        self._settings = settings
        self._client = anthropic.Anthropic(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_s,
            max_retries=2,
        )
        self.label = f"Anthropic · {settings.model}"

    def extract(self, deck: Deck) -> LLMDraft:
        anthropic = self._anthropic
        model = self._settings.model
        kwargs: dict[str, object] = {}
        if self._settings.effort and self._settings.effort != "none":
            kwargs["output_config"] = {"effort": self._settings.effort}
        if model in _FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            response = self._client.beta.messages.parse(
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_prompt(deck)}],
                output_format=LLMDraft,
                **kwargs,  # type: ignore[arg-type]
            )
        except anthropic.AuthenticationError as exc:
            raise ConfigError("The Anthropic API rejected the API key (ANTHROPIC_API_KEY).") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ConfigError("The Anthropic API key lacks permission for this model.") from exc
        except anthropic.NotFoundError as exc:
            raise ConfigError(f"Model '{model}' was not found. Check TEASER_LLM_MODEL.") from exc
        except anthropic.RateLimitError as exc:
            raise ExtractionError("The Anthropic API rate limit was reached. Try again shortly.") from exc
        except anthropic.BadRequestError as exc:
            raise ExtractionError(f"The Anthropic API rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"Anthropic API error (HTTP {exc.status_code}). Try again later.") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("Could not reach the Anthropic API. Check your network.") from exc
        except ValidationError as exc:
            raise ExtractionError("The model's output did not match the teaser schema.") from exc

        if response.stop_reason == "refusal":
            raise ExtractionError("The model declined to process this deck.")
        if response.stop_reason == "max_tokens":
            raise ExtractionError("The model's output was cut off. Try a shorter deck.")
        parsed = response.parsed_output
        if parsed is None:
            raise ExtractionError("The model returned no structured output.")
        return parsed


class OpenAICompatibleProvider:
    """Any OpenAI-compatible chat endpoint (OpenAI, Azure OpenAI, vLLM, Ollama, ...).

    Uses plain JSON mode plus local validation, because schema-constrained modes differ
    across compatible servers. One retry is made with the validation error attached.
    """

    def __init__(self, settings: Settings) -> None:
        import openai

        self._openai = openai
        self._settings = settings
        self._client = openai.OpenAI(
            api_key=settings.api_key, base_url=settings.base_url, timeout=settings.timeout_s
        )
        self.label = f"OpenAI-compatible · {settings.model}"

    def extract(self, deck: Deck) -> LLMDraft:
        openai = self._openai
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + json_schema_instruction()},
            {"role": "user", "content": build_user_prompt(deck)},
        ]
        last_error = ""
        for _attempt in range(2):
            try:
                response = self._client.chat.completions.create(
                    model=self._settings.model,
                    messages=messages,  # type: ignore[arg-type]
                    response_format={"type": "json_object"},
                )
            except openai.AuthenticationError as exc:
                raise ConfigError("The API rejected the key (OPENAI_API_KEY).") from exc
            except openai.NotFoundError as exc:
                raise ConfigError(f"Model '{self._settings.model}' was not found.") from exc
            except openai.APIStatusError as exc:
                raise ExtractionError(f"Model API error (HTTP {exc.status_code}).") from exc
            except openai.APIConnectionError as exc:
                raise ExtractionError("Could not reach the model API. Check OPENAI_BASE_URL.") from exc
            content = response.choices[0].message.content or ""
            try:
                return LLMDraft.model_validate_json(content)
            except ValidationError as exc:
                last_error = str(exc)[:2000]
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That JSON failed validation:\n{last_error}\nReturn corrected JSON only."},
                ]
        raise ExtractionError("The model's output did not match the teaser schema after a retry.")


def check_connection(settings: Settings) -> str:
    """Verify the key and model without generating any tokens (a model lookup, not a message).

    Returns a short success description; raises ConfigError or ExtractionError otherwise.
    """
    settings.require_ready()
    if settings.provider == "offline":
        return "Offline mode: no API connection needed."
    if settings.provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=settings.api_key, base_url=settings.base_url, timeout=30, max_retries=1)
        try:
            model = client.models.retrieve(settings.model)
        except anthropic.AuthenticationError as exc:
            raise ConfigError("The Anthropic API rejected the API key (ANTHROPIC_API_KEY).") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ConfigError("The API key is valid but lacks access to this model.") from exc
        except anthropic.NotFoundError as exc:
            raise ConfigError(f"Model '{settings.model}' was not found. Check TEASER_LLM_MODEL.") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"Anthropic API error (HTTP {exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("Could not reach the Anthropic API. Check your network.") from exc
        return f"Connected to Anthropic. Model available: {model.display_name} ({model.id})."
    import openai

    client = openai.OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=30)
    try:
        client.models.retrieve(settings.model)
    except openai.AuthenticationError as exc:
        raise ConfigError("The API rejected the key (OPENAI_API_KEY).") from exc
    except openai.NotFoundError as exc:
        raise ConfigError(f"Model '{settings.model}' was not found.") from exc
    except (openai.APIStatusError, openai.APIConnectionError) as exc:
        raise ExtractionError("Could not verify the model API connection.") from exc
    return f"Connected. Model available: {settings.model}."


def make_provider(settings: Settings) -> Provider:
    """Build the configured provider, failing early with a clear message."""
    settings.require_ready()
    if settings.provider == "anthropic":
        return AnthropicProvider(settings)
    if settings.provider == "openai":
        return OpenAICompatibleProvider(settings)
    from teaser.extract.offline import OfflineExtractor

    return OfflineExtractor()
