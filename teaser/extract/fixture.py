"""A provider that replays a stored model output. Used for the demo and the tests.

The app offers it only for the bundled synthetic sample deck, and labels the result as a
fixture so nobody mistakes it for a live model call.
"""

from __future__ import annotations

from pathlib import Path

from teaser.config import PROJECT_ROOT
from teaser.ingest.models import Deck
from teaser.schema import LLMDraft

SAMPLE_OUTPUT = PROJECT_ROOT / "samples" / "sample_model_output.json"


class FixtureProvider:
    """Returns a stored ``LLMDraft`` regardless of the deck."""

    def __init__(self, path: Path = SAMPLE_OUTPUT, label: str = "Recorded sample output (fixture, no model call)") -> None:
        self._draft = LLMDraft.model_validate_json(path.read_text(encoding="utf-8"))
        self.label = label

    def extract(self, deck: Deck) -> LLMDraft:
        return self._draft.model_copy(deep=True)
