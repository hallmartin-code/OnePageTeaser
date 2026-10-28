"""Errors the user can see. Every message is written to be shown in the interface as-is."""

from __future__ import annotations


class TeaserError(Exception):
    """Base class for every failure the app reports to the user."""


class ConfigError(TeaserError):
    """Missing or invalid configuration, such as an absent API key."""


class IngestError(TeaserError):
    """The deck could not be read, parsed, or converted."""


class UnsupportedFormatError(IngestError):
    """The file is not a PDF, PPTX, or PPT."""


class PasswordRequiredError(IngestError):
    """The PDF is encrypted and no (or a wrong) password was supplied."""


class ExtractionError(TeaserError):
    """The model call failed or returned output that did not validate."""


class LayoutOverflowError(TeaserError):
    """The content does not fit on one page at the minimum font size."""

    def __init__(self, message: str, *, overflow_lines: int, words: int) -> None:
        super().__init__(message)
        self.overflow_lines = overflow_lines
        self.words = words


class RenderError(TeaserError):
    """The PDF could not be produced or failed validation."""
