"""Verify the configured model API, and optionally run one live analysis end to end.

    python scripts/check_api.py                 # key + model check only (no tokens used)
    python scripts/check_api.py --run-sample    # also analyse the synthetic sample deck (billed)
    python scripts/check_api.py --run-sample --deck path/to/deck.pdf

Reads ANTHROPIC_API_KEY / TEASER_LLM_* from the environment or .env. The key is never
printed. --run-sample sends the deck's extracted text to the configured provider and
writes the teaser to samples/output/live_teaser.pdf. Approvals are NOT granted here: the
script reports what a reviewer would still need to approve, and renders a preview with
the content as drafted.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from teaser.config import load_settings  # noqa: E402
from teaser.editing import export_blockers, word_count  # noqa: E402
from teaser.errors import TeaserError  # noqa: E402
from teaser.extract import generate_draft, make_provider  # noqa: E402
from teaser.extract.providers import check_connection  # noqa: E402
from teaser.ingest import load_deck  # noqa: E402
from teaser.render import layout_audit, page_count, preview_png, render_pdf  # noqa: E402
from teaser.schema import FlagKind  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-sample", action="store_true", help="Run a billed analysis of a deck.")
    parser.add_argument("--deck", type=Path, default=ROOT / "samples" / "sample_deck.pdf")
    parser.add_argument("--out", type=Path, default=ROOT / "samples" / "output" / "live_teaser.pdf")
    args = parser.parse_args()

    try:
        settings = load_settings()
        print("Configuration:", ", ".join(f"{k}: {v}" for k, v in settings.describe().items()))
        print(check_connection(settings))
        if not args.run_sample:
            return 0

        deck = load_deck(args.deck.read_bytes(), args.deck.name, enable_ocr=settings.enable_ocr)
        print(f"Read {deck.filename}: {deck.page_count} pages. Sending extracted text to {settings.provider_label}...")
        started = time.monotonic()
        draft = generate_draft(deck, make_provider(settings))
        print(f"Draft generated in {time.monotonic() - started:.0f}s: {len(draft.items)} items, "
              f"{sum(i.include for i in draft.items)} included, {word_count(draft)} words.")
    except TeaserError as exc:
        print(f"FAILED: {exc}")
        return 1

    accuracy = [f for f in draft.flags if f.kind in (FlagKind.UNSUPPORTED, FlagKind.FIGURE_MISMATCH)]
    print(f"Accuracy flags (unsupported / figure mismatch): {len(accuracy)}")
    for flag in accuracy:
        print("  -", flag.message)
    print(f"Open approvals before export: {len(export_blockers(draft))}")

    try:
        result = render_pdf(draft)
    except TeaserError as exc:
        print(f"Render: {exc}")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(result.pdf)
    args.out.with_suffix(".png").write_bytes(preview_png(result.pdf, dpi=130))
    print(f"Wrote {args.out} (pages={page_count(result.pdf)}, body={result.report.body_size:g}pt, "
          f"layout issues={len(layout_audit(result.pdf))}). Preview: {args.out.with_suffix('.png')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
