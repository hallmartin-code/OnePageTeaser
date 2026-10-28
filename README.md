# One-Page Investor Teaser

A Streamlit app that turns an investor pitch deck (PDF, PPTX, or legacy PPT) into a
reviewed, one-page US Letter teaser PDF suitable for broad investor distribution.

The deck is the only factual source. Every claim keeps its page reference. Confidential
material is excluded or held for explicit approval. The PDF is guaranteed to be exactly
one page, or the app asks you to shorten the content; it never clips or silently truncates.

![Sample teaser](samples/output/sample_teaser_fixture.png)

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt     # requirements.txt alone for production

copy .env.example .env            # macOS/Linux: cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY, or set TEASER_LLM_PROVIDER=offline

streamlit run app.py
```

Then open http://localhost:8501.

**No API key?** Click **Use synthetic sample deck**, choose **Recorded sample output
(fixture)** or **Offline extractive**, and generate. Both run with no model and send
nothing anywhere.

Requires Python 3.11+. Tested on Python 3.14 / Windows 11.

---

## Document template

Every teaser follows one company-neutral structure template:

- [`templates/teaser_template.md`](templates/teaser_template.md) is the readable spec:
  layout, header fields, the seven sections and the fields to analyse in each, typography,
  footer, length targets, and review rules.
- [`templates/teaser_template.json`](templates/teaser_template.json) is the machine-readable
  version the app loads at start-up (`teaser/template.py`).

The JSON supplies the section order, default and industry-specific headings, section
purposes, the fields the model is asked to extract, the disclaimer, page geometry, font
sizes, and word targets. Edit the JSON to change the structure; `tests/test_template.py`
checks that the JSON, the Markdown spec, and the code agree, and that the template contains
no company data.

---

## Using your Claude Console API key

1. Create a key at console.anthropic.com → **API Keys**.
2. Put it in `.env` (never commit this file):
   ```
   TEASER_LLM_PROVIDER=anthropic
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Verify the key and model. This is a model lookup, so it is free:
   ```bash
   python scripts/check_api.py
   ```
4. Optionally run one billed analysis of the synthetic deck end to end:
   ```bash
   python scripts/check_api.py --run-sample
   ```
   This writes `samples/output/live_teaser.pdf` and `.png`. Add `--deck path/to/deck.pdf`
   to try a real deck.

In the app, the sidebar's **Test API connection** button runs the same free check. Drafts
use `claude-opus-5-5` at `high` effort by default; change `TEASER_LLM_MODEL` or
`TEASER_LLM_EFFORT` to trade quality for cost and speed. The extraction prompt and the PDF
layout both follow `templates/teaser_template.json`.

---

## Result emails (Resend)

When `RESEND_API_KEY` is set, results are emailed automatically to `REPORT_EMAIL_TO`
(default **Info@tencapital.group**). Two events trigger an email:

| Event | When | Attachment |
|---|---|---|
| Draft analysis | Each time a draft is generated | `<Company>_Investor_Teaser_DRAFT.pdf`, the teaser as drafted (included items only) |
| Approved teaser | When a reviewer downloads the approved PDF | Final `.pdf` and `.docx` |

Each email body shows:

- a summary: pages read, word count, items included, and open approvals;
- which sections are present and which are missing from the deck;
- the confidentiality screening, listing **category, pages, and status only**;
- accuracy and data-quality notes.

Excluded content never appears in an email or its attachments.

**Behaviour:**

- **Failures don't stop the app.** A failed send is reported in the app (for example, an
  unverified sender domain) and never blocks drafting or download.
- **No duplicates.** Each send carries a Resend idempotency key, so Streamlit reruns can't
  resend the same email.
- **Sample runs are skipped.** Runs of the bundled synthetic sample deck aren't emailed
  unless `EMAIL_DEMO_RUNS=true`.
- **Sender address.** Emails come from `REPORT_EMAIL_FROM` (default
  `TEN Capital Investor Teaser <teaser@tencapital.group>`). Its domain must be verified in
  the Resend account that owns the key.

---

## Deploying to Railway

The repo includes `railway.json`, `nixpacks.toml` (Python 3.12, headless OpenCV for OCR),
a `Procfile`, and `.railwayignore`. `.env` is never uploaded.

```bash
railway login                       # opens a browser
railway init -n onepage-teaser      # or: railway link (existing project)
railway up --detach                 # build and deploy from this folder
railway domain                      # generate a public *.up.railway.app URL
```

Then set these variables in the Railway dashboard (service → **Variables**), or with
`railway variables --set "NAME=value"`:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Console key |
| `APP_PASSWORD` | a strong password, shared only with users |
| `TEASER_LLM_PROVIDER` | `anthropic` (the default) |
| `RESEND_API_KEY` | your Resend key (enables result emails) |
| `REPORT_EMAIL_TO` | optional; defaults to `Info@tencapital.group` |
| `REPORT_EMAIL_FROM` | optional; must be on a Resend-verified domain |

Setting variables triggers a redeploy. The health check is `/_stcore/health`.

**Security and operations:**

- **Password required.** On Railway the app refuses to run without `APP_PASSWORD`, so a
  public URL can't spend your API credits or receive decks from strangers. The password is
  compared in constant time and is never displayed.
- **Spend limits.** Set a monthly spend limit for the key in the Claude Console.
- **Uploads stay in memory.** The container doesn't write uploads to disk, and a redeploy
  clears all sessions.
- **Legacy `.ppt` needs LibreOffice.** To accept `.ppt` uploads, uncomment
  `libreoffice-fresh` in `nixpacks.toml`. This adds about 500 MB to the image.
- **Large decks.** A large deck at `high` effort can take a few minutes to draft. Streamlit
  keeps the session open over its websocket while it runs.

---

## Workflow

1. **Upload** a PDF, PPTX, or PPT. Password-protected PDFs prompt for the password.
2. **Extract.** Text, tables (flattened to `label: a | b | c` rows), and OCR text for
   image-only pages are read per page. Expand *Extracted text by page* to see exactly
   what the draft is based on.
3. **Generate a draft** with the configured model, or offline. If the model is an
   external service, you must tick a consent box first.
4. **Review and edit.** Each section shows its bullets with:
   - page references, marked ✓ (quote found on that page) or ? (not found);
   - a *Show source quotes* popover;
   - inline flags: ⛔ needs approval, ⚠️ warning, ℹ️ note.

   Edit any text, untick **Use** to drop an item, add items, and rename headings.
   Every edit is re-checked against the cited pages immediately.
5. **Approve.** The review panel summarises confidentiality categories by page, plus
   accuracy issues, conflicts, missing sections, and open approvals.
6. **Preview and download.** The page re-renders on every change. The PDF download is
   enabled only when:
   - every blocking flag on included content is approved or the item is excluded;
   - the layout check passes;
   - you confirm you have read the full teaser.

   An editable DOCX is offered alongside the PDF.

---

## Configuration

All settings are environment variables (or entries in `.env`). Credentials are never
logged or shown; the sidebar only says whether a key is configured.

| Variable | Default | Meaning |
|---|---|---|
| `TEASER_LLM_PROVIDER` | `anthropic` | `anthropic`, `openai` (any OpenAI-compatible endpoint), or `offline` |
| `TEASER_LLM_MODEL` | `claude-opus-5-5` for anthropic | Model name; required for `openai` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | | Anthropic credentials / endpoint |
| `APP_PASSWORD` | | Web app password; required on Railway |
| `RESEND_API_KEY` | | Enables result emails via Resend |
| `REPORT_EMAIL_TO` | `Info@tencapital.group` | Comma-separated recipients |
| `REPORT_EMAIL_FROM` | `TEN Capital Investor Teaser <teaser@tencapital.group>` | Sender; domain must be verified in Resend |
| `EMAIL_DEMO_RUNS` | `false` | Also email runs of the synthetic sample deck |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | | OpenAI, Azure OpenAI, vLLM, Ollama, etc. |
| `TEASER_LLM_EFFORT` | `high` | Anthropic effort level; `none` to omit |
| `TEASER_LLM_TIMEOUT` | `300` | Seconds per request |
| `TEASER_ENABLE_OCR` | `true` | OCR for scanned / image-only pages (needs `rapidocr-onnxruntime`) |
| `TEASER_TEN_FOOTER` | `true` | Add the TEN Capital standard footer (also a sidebar toggle) |

**Providers:**

- **`anthropic`** uses the official SDK with schema-constrained output (`messages.parse`
  with the Pydantic schema). The server-side refusal fallback is enabled on models that
  support it.
- **`openai`** uses JSON mode plus local Pydantic validation, with one corrective retry.
- **`offline`** classifies pages by title and lifts deck lines verbatim with their page
  numbers. Nothing is summarised, so expect to edit for length.

This app does not use the Claude Code extension for model access; it calls the
configured API directly.

---

## Accuracy safeguards

- **Source rules in the prompt.** The deck is the only source, wrapped in a delimited
  `<deck_content>` block and described as untrusted data. Text inside it that addresses
  an AI ("ignore previous instructions…") is reported, not followed. A deterministic
  detector flags such pages too.
- **Quote verification.** Every cited quote must fuzzily match text on the cited page.
  Otherwise the item is flagged *unsupported*, and export is blocked until it is
  confirmed or corrected.
- **Figure checks.** Every number in an item is parsed with its magnitude, unit, and
  currency, and must appear on a cited page. `$4.2B` against `$4.2M`, `€` against `$`,
  or `15M` against `1.5 million` is flagged.
- **Qualifier drift.** Wording stronger than its source is flagged:
  - a forecast stated as fact;
  - an LOI or discussion described as signed;
  - a planned submission described as approved.
- **Conflicts.** Different TAM/SAM/SOM or funding-ask figures on different pages are
  flagged.
- **No invented header.** A tagline or website that doesn't appear in the deck is dropped
  and shown as missing.
- **Missing data stays visible.** Missing sections and fields are listed in the review
  panel and omitted from the PDF.

## Confidentiality safeguards

A deterministic screen (`teaser/screening.py`) runs on every page and every item,
independently of the model:

| Category | Default |
|---|---|
| Investor names / identities | Excluded |
| Committed or individual investment amounts | Excluded |
| Undisclosed financing / commercial negotiations (LOIs, term sheets, NDAs) | Excluded |
| Deal terms, valuation, caps, cap table | Excluded |
| Proprietary structures, formulas, sequences, recipes | Excluded |
| Detailed IP strategy, unpublished patent information | Excluded |
| Personal contact details | Excluded |
| Customer / partner identities | Excluded |
| Funding ask | Included, but must be approved |
| Content from slides marked confidential | Must be approved (whole-deck marking needs a deck-level approval) |

Excluded items stay available in a collapsed *Excluded by default* list. Reinstating one
requires approving its specific flag. The review panel shows categories and page numbers
only; it does not reproduce the sensitive text. The funding ask (role `funding_ask`) and
committed capital (role `committed_capital`) are kept separate throughout.

Automated screening is a first pass. It can miss things and can flag harmless text. A
person must read the final teaser before distribution; the download requires that
confirmation.

## Data handling

- Uploads are held in memory for the session. Nothing is written to disk, except a
  temporary file for LibreOffice `.ppt` conversion, which is deleted immediately.
- Deck text is sent to an external provider only when you choose the model option and
  tick the consent box. Offline and fixture modes send nothing.
- Result emails (when enabled) go to Resend and then to `REPORT_EMAIL_TO`. They contain
  the teaser content and category/page-level screening results, never excluded content.
- No deck content, prompts, responses, or keys are logged. Streamlit usage statistics are
  disabled in `.streamlit/config.toml`.
- Speaker notes in PPTX files are not read; they are presenter remarks, not deck content.

## Document design

- US Letter portrait. The header has a brand-colour bar, the company name, an italic
  tagline, and the website (right-aligned, or on its own line if the name is long).
- Two balanced columns, with sections kept whole. The funding section is a tinted box.
  TAM/SAM/SOM labels and team names are bolded.
- Body text is 10.5 pt, stepping down to a 9 pt floor only when needed. The disclaimer is
  9 pt. The optional TEN Capital footer is 7 pt Open Sans, per the TEN document standard.
- If the content doesn't fit at 9 pt, export is refused and the error says roughly how
  many lines and words to cut. Every render is checked for exactly one page, text inside
  the margins, and no overlapping text spans.
- **Brand colour.** Taken from the PPTX theme (stock Office colours are ignored) or the
  dominant colour on the PDF cover. It is darkened for legibility, and falls back to a
  restrained navy. You can override it in the app.
- **Logo.** Off by default. Include one only if you may reuse it: pick an image from the
  cover page or upload one.

---

## Tests

```bash
python -m pytest
```

There are 104 tests:

| File | Covers |
|---|---|
| `test_source_fidelity.py` | Units, magnitudes, and currencies; fabricated figures; unsupported quotes; page re-attribution; non-existent pages; qualifier drift; invented headers; re-check after edit with approvals preserved |
| `test_confidentiality.py` | Every category, false-positive guards, default exclusion, funding ask vs committed capital, confidential slides and whole-deck marking, no sensitive text in the panel or the PDF, prompt-injection handling |
| `test_missing_information.py` | Missing sections and fields reported and omitted; missing company name blocks export; offline mode invents nothing |
| `test_pdf_pagination.py` | Exactly one page; 9 pt floor; overflow refused rather than clipped; no truncation; Unicode; long headers; logo; overlap detection; DOCX |
| `test_ingest.py` | PDF/PPTX references, table flattening, scanned and image-only pages, password PDFs, corrupt and unsupported files, `.ppt` without LibreOffice, extension spoofing |
| `test_providers.py` | Configuration errors, key and password never exposed, hosted-without-password block, connection check, Anthropic request shape and stop reasons, OpenAI-compatible retry (fake clients, no network) |
| `test_template.py` | Template validates, holds no company data, matches the Markdown spec and the code, and drives the prompt and section order |
| `test_mailer.py` | Default recipient, payload shape and attachments, no sensitive text in emails, idempotency keys, headers, failures that never raise |
| `test_app.py` | The Streamlit script end to end, headless (`AppTest`): sample deck → draft → approvals → download gating; password gate; hosted deployment without a password refuses to run; draft emailed once per generation; sample runs not emailed by default |

## Sample data

Everything in `samples/` is synthetic. *Halcyon Therapeutics* is fictional.

- `samples/sample_deck.pdf` and `.pptx`, generated by `python scripts/make_sample_deck.py`.
  The deck deliberately includes:
  - investor names, committed capital, and a valuation;
  - a SMILES string and a provisional patent number;
  - contact details and an LOI;
  - two slides marked confidential;
  - a TAM conflict;
  - a prompt-injection line;
  - an image-only slide that needs OCR.
- `samples/sample_model_output.json` is a hand-written structured output in the exact
  schema the model returns. It powers the fixture demo and the tests.
- `samples/output/` holds example results from `python scripts/generate_sample_teaser.py`.

## Project layout

```
app.py                      Streamlit interface
templates/                  document structure template (Markdown spec + JSON the app loads)
teaser/
  config.py                 environment settings
  template.py               loads and validates templates/teaser_template.json
  schema.py                 Pydantic models: model output, editable draft, flags
  ingest/                   PDF (PyMuPDF), PPTX (python-pptx), PPT (LibreOffice), OCR
  extract/                  prompts, providers (Anthropic / OpenAI-compatible), offline, fixture, pipeline
  fidelity.py               quote, figure, and qualifier checks
  screening.py              deterministic confidentiality screen
  editing.py                flags, approvals, re-checks, export gating
  mailer.py                 Resend result emails (draft and approved teaser)
  brand.py                  brand colour detection
  render/                   ReportLab PDF (fitting, audit), DOCX export, fonts
assets/                     Open Sans (SIL OFL), TEN Capital footer logo
```

## Limitations

- **Screening is pattern-based.** Investor and customer names are caught through
  context phrases ("led by", "customers include", firm-like names near funding language).
  A bare name with no context can slip through; the model's own tagging and the human
  review are the backstops.
- **Figure checks compare numbers, not meaning.** A number copied correctly into the
  wrong context passes, and a figure written in words beyond "twelve" is not recognised.
- **Charts and diagrams embedded as pictures** are read only through OCR text. Chart
  values drawn as vectors in PDFs are read only if they are text.
- **PPTX slide images need LibreOffice.** Without it, logos come only from cover-slide
  picture shapes, and colour detection uses the theme.
- **The offline mode is extractive.** It copies deck lines and classifies pages by
  keywords, so expect to edit.
- **The DOCX is editable,** so Word may push it past one page after edits. The PDF is the
  controlled distribution copy.
- **Web lookups are not implemented.** The deck is always the sole source.
- **No live model call was made during development** (no API key was available). The
  Anthropic and OpenAI-compatible adapters are tested against fake clients only.
