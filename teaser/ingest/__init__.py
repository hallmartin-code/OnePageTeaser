"""Deck ingestion: PDF, PPTX, and legacy PPT into a format-neutral ``Deck``."""

from teaser.ingest.models import Deck, Page
from teaser.ingest.router import SUPPORTED, load_deck

__all__ = ["SUPPORTED", "Deck", "Page", "load_deck"]
