"""Generate sample teasers from the synthetic deck, without an API key.

Runs the same pipeline as the app (ingest -> draft -> verify/screen -> render), using the
recorded fixture output and the offline extractor. Approvals are granted programmatically
here, which only makes sense for this synthetic deck; in the app a person must do it.

    python scripts/generate_sample_teaser.py [--deck samples/sample_deck.pdf] [--out samples/output]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from teaser.editing import approve_confidential_pages, export_blockers, word_count  # noqa: E402
from teaser.extract import generate_draft  # noqa: E402
from teaser.extract.fixture import FixtureProvider  # noqa: E402
from teaser.extract.offline import OfflineExtractor  # noqa: E402
from teaser.ingest import load_deck  # noqa: E402
from teaser.render import RenderOptions, layout_audit, page_count, preview_png, render_pdf  # noqa: E402
from teaser.render.docx_export import render_docx  # noqa: E402
from teaser.schema import SensitiveCategory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deck", type=Path, default=ROOT / "samples" / "sample_deck.pdf")
    parser.add_argument("--out", type=Path, default=ROOT / "samples" / "output")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    deck = load_deck(args.deck.read_bytes(), args.deck.name)
    print(f"Read {deck.filename}: {deck.page_count} pages; warnings: {len(deck.warnings)}")
    for name, provider in (("fixture", FixtureProvider()), ("offline", OfflineExtractor())):
        draft = generate_draft(deck, provider)
        # Synthetic deck only: approve confidential-page content and the funding ask.
        approve_confidential_pages(draft)
        for flag in draft.flags:
            if flag.category is SensitiveCategory.FUNDING_ASK:
                flag.approved = True
        blockers = export_blockers(draft)
        if blockers:
            print(f"[{name}] not exportable:", *blockers, sep="\n  - ")
            continue
        result = render_pdf(draft, RenderOptions())
        stem = args.out / f"sample_teaser_{name}"
        stem.with_suffix(".pdf").write_bytes(result.pdf)
        stem.with_suffix(".png").write_bytes(preview_png(result.pdf, dpi=130))
        stem.with_suffix(".docx").write_bytes(render_docx(draft))
        audit = layout_audit(result.pdf)
        print(f"[{name}] {stem.with_suffix('.pdf').name}: pages={page_count(result.pdf)} "
              f"body={result.report.body_size:g}pt words={word_count(draft)} "
              f"fill={result.report.fill_ratio:.0%} layout_issues={len(audit)}")
        for issue in audit:
            print("   ", issue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
