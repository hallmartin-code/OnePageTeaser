"""Runtime configuration, read from the environment (and an optional ``.env`` file).

Nothing here is ever logged. ``Settings.describe()`` is the only thing the UI shows, and it
reports whether a key is present, never its value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from teaser.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS = PROJECT_ROOT / "assets"

PROVIDERS = ("anthropic", "openai", "offline")

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "openai": "",  # no sensible universal default for OpenAI-compatible endpoints
    "offline": "extractive",
}


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Resolved configuration."""

    provider: str
    model: str
    api_key: str | None
    base_url: str | None
    effort: str
    timeout_s: float
    enable_ocr: bool
    ten_footer: bool
    resend_api_key: str | None = None
    report_email_to: str = "Info@tencapital.group"
    report_email_from: str = "TEN Capital Investor Teaser <teaser@tencapital.group>"
    email_demo_runs: bool = False

    @property
    def email_enabled(self) -> bool:
        """Whether analysis results are emailed (needs a Resend key and a recipient)."""
        return bool(self.resend_api_key and self.email_recipients)

    @property
    def email_recipients(self) -> list[str]:
        """REPORT_EMAIL_TO split on commas."""
        return [a.strip() for a in self.report_email_to.split(",") if a.strip()]

    @property
    def sends_externally(self) -> bool:
        """Whether generating a draft transmits deck content to a third party."""
        return self.provider != "offline"

    @property
    def provider_label(self) -> str:
        """Human label for the interface."""
        if self.provider == "anthropic":
            return "Anthropic Claude API"
        if self.provider == "openai":
            return f"OpenAI-compatible API ({self.base_url or 'api.openai.com'})"
        return "Offline extractive mode (no model, nothing leaves this machine)"

    def require_ready(self) -> None:
        """Raise a clear ConfigError if the selected provider cannot run."""
        if self.provider == "offline":
            return
        if not self.model:
            raise ConfigError(
                f"TEASER_LLM_MODEL is not set. Choose a model for the '{self.provider}' provider."
            )
        if not self.api_key:
            var = "ANTHROPIC_API_KEY" if self.provider == "anthropic" else "OPENAI_API_KEY"
            raise ConfigError(
                f"No API key found for the '{self.provider}' provider. Set {var} in your "
                f"environment or .env file, or set TEASER_LLM_PROVIDER=offline."
            )

    def describe(self) -> dict[str, str]:
        """Safe summary for display. Never includes credentials."""
        return {
            "Provider": self.provider_label,
            "Model": self.model or "(not set)",
            "API key": "configured" if self.api_key else "not set",
            "OCR": "enabled" if self.enable_ocr else "disabled",
            "Result emails": f"to {', '.join(self.email_recipients)}" if self.email_enabled else "off",
        }


def load_settings(env_file: Path | None = None) -> Settings:
    """Read settings from the environment, loading ``.env`` from the project root first."""
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    provider = os.environ.get("TEASER_LLM_PROVIDER", "anthropic").strip().lower()
    if provider not in PROVIDERS:
        raise ConfigError(
            f"TEASER_LLM_PROVIDER={provider!r} is not supported. Use one of: {', '.join(PROVIDERS)}."
        )

    model = os.environ.get("TEASER_LLM_MODEL", "").strip() or DEFAULT_MODELS[provider]
    if provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY") or None
        base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
    elif provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY") or None
        base_url = os.environ.get("OPENAI_BASE_URL") or None
    else:
        api_key, base_url = None, None

    effort = os.environ.get("TEASER_LLM_EFFORT", "high").strip().lower()
    try:
        timeout_s = float(os.environ.get("TEASER_LLM_TIMEOUT", "300"))
    except ValueError as exc:
        raise ConfigError("TEASER_LLM_TIMEOUT must be a number of seconds.") from exc

    return Settings(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        effort=effort,
        timeout_s=timeout_s,
        enable_ocr=_flag("TEASER_ENABLE_OCR", True),
        ten_footer=_flag("TEASER_TEN_FOOTER", True),
        resend_api_key=(os.environ.get("RESEND_API_KEY") or "").strip() or None,
        report_email_to=os.environ.get("REPORT_EMAIL_TO", "").strip() or "Info@tencapital.group",
        report_email_from=os.environ.get("REPORT_EMAIL_FROM", "").strip()
        or "TEN Capital Investor Teaser <teaser@tencapital.group>",
        email_demo_runs=_flag("EMAIL_DEMO_RUNS", False),
    )
