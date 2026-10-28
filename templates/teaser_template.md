# One-Page Investor Teaser — Document Structure Template

This is the general template for every teaser the app generates. It defines structure, fields,
and formatting only; it holds no company data. The machine-readable version the app loads is
[`teaser_template.json`](teaser_template.json); `tests/test_template.py` keeps the two in step
with the code.

`{{field}}` marks a value filled from the deck. `[[...]]` marks a repeating item.

---

## 1. Page

| Property | Value |
|---|---|
| Size / orientation | US Letter, portrait |
| Pages | Exactly 1 |
| Margins | 0.6 in left/right, 0.5 in top, 0.45 in bottom |
| Body | 2 columns, 0.3 in gutter; sections kept whole, columns balanced in reading order |
| Overflow | Never clipped, truncated, or continued; export refused below the minimum body size |

## 2. Layout

```
┌────────────────────────────────────────────────────────────────────┐
│████████████████████ brand bar (primary colour) ████████████████████│
│ [logo]  {{company_name}}                               {{website}} │
│         {{tagline}}                                                 │
│────────────────────────────────────────────────────────────────────│
│ {{heading: problem_market}}        │ {{heading: traction}}          │
│ • [[problem / opportunity item]]   │ • [[milestone item]]           │
│                                    │                                │
│ {{heading: solution_technology}}   │ {{heading: team}}              │
│ • [[solution item]]                │ • **{{name}}**, {{title}} -    │
│                                    │   {{credentials}}              │
│ {{heading: pipeline_product}}      │                                │
│ • [[{{asset}} ({{use}}): {{stage}}]]│ ┃ {{heading: funding}}         │
│                                    │ ┃ • **{{funding_ask}}**        │
│ {{heading: market_size}}           │ ┃ • [[use of funds item]]      │
│ • **TAM** {{tam}}                  │ ┃   (tinted box)               │
│ • **SAM** {{sam}}                  │                                │
│ • **SOM** {{som}}                  │                                │
│────────────────────────────────────────────────────────────────────│
│        For informational purposes only. Does not constitute        │
│                   an offer to sell securities.                     │
│  {{company_name}} Investor Teaser  1  Compiled on {{date}} by TEN  │
│                         Capital Network  [TEN logo]                │
└────────────────────────────────────────────────────────────────────┘
```

The column split shown is illustrative; the renderer chooses it to balance heights.

## 3. Typography

| Element | Style |
|---|---|
| Font | Open Sans (fallback Helvetica) |
| Body | 10.5 pt, stepping down by 0.5 to a 9 pt minimum; line height 1.32 |
| Section heading | Body + 0.5 pt, bold, primary colour, thin accent rule beneath |
| Company name | Bold, primary colour, 22 pt (16 pt minimum; wraps if longer) |
| Tagline | 11 pt italic, muted |
| Website | 10 pt, muted |
| Disclaimer | 9 pt italic, centred |
| TEN footer | Open Sans 7 pt, centred |

Colours: brand primary when reliably identified (darkened to ≥ 5:1 contrast on white),
otherwise navy `#1F3A5F` / accent `#4A6F94`; body `#1E2329`; muted `#555C66`.

## 4. Header fields

| Field | Required | Source rule | Placement |
|---|---|---|---|
| `company_name` | Yes | Verbatim from deck | Left, bold |
| `tagline` | No | Verbatim from deck; never invented | Below name |
| `website` | No | Verbatim from deck; never invented | Right of name, or own line if the name is long |
| Logo | No (off by default) | Only if available and cleared for reuse | Left of name |

## 5. Sections

Order is fixed. A section with no supported content is omitted from the PDF and reported as
missing in the review interface. Headings may be renamed per industry (see §6).

| # | Key | Default heading | Fields to analyse | Item roles | Format |
|---|---|---|---|---|---|
| 1 | `problem_market` | Problem & Opportunity | Problem; who is affected and scale; cost/impact; why current solutions fall short | general | Bullets |
| 2 | `solution_technology` | Solution & Technology | What it is; how it works; differentiators; validation status | general | Bullets |
| 3 | `pipeline_product` | Product Pipeline | Product/asset; indication or use case; stage; next planned milestone | general | `Name (use): stage` |
| 4 | `market_size` | Market Size | TAM (scope, year); SAM (scope); SOM (basis) | tam, sam, som, general | Bold TAM/SAM/SOM label |
| 5 | `traction` | Key Milestones | Completed milestones; validation data; grants/awards; granted IP; commercial traction | general | Bullets |
| 6 | `team` | Leadership Team | Name; title; notable credentials | team_member | `**Name**, Title - credentials` |
| 7 | `funding` | Funding Ask & Use of Funds | Amount and round; use of funds; runway/milestones funded | funding_ask, use_of_funds, general | Tinted box; ask in bold |

A single-item section renders as a paragraph rather than a bullet.

## 6. Industry heading variants

| Industry | `pipeline_product` | `solution_technology` | `traction` |
|---|---|---|---|
| biotech | Pipeline | Science & Platform | Key Milestones |
| medtech | Product Pipeline | — | Clinical & Regulatory Milestones |
| software | Product | — | Traction |

## 7. Footer

1. Disclaimer, fixed and not editable:
   *For informational purposes only. Does not constitute an offer to sell securities.*
2. TEN Capital footer (on by default, toggleable):
   `[Document Title]   [PAGE#]   Compiled on [DATE] by TEN Capital Network   [TEN Capital Logo]`,
   where Document Title is `{{company_name}} Investor Teaser`.

## 8. Length

350–500 words across tagline and included items; items of 8–30 words. Shorter is acceptable
when the deck supports no more — content is never padded.

## 9. Per-item fields (review interface)

| Field | Meaning |
|---|---|
| `text` | Teaser wording; facts only from cited pages |
| `section` | Section key from §5 |
| `role` | general · tam · sam · som · funding_ask · use_of_funds · committed_capital · team_member |
| `refs` | `{page, quote, verified}` — shown in review only, never printed |
| `claim_type` | actual · forecast · planned · descriptive |
| `sensitive_category` | Confidentiality category, if any |
| `include` | Whether the item is exported |

## 10. Content rules

- The deck is the only source. Units, currencies, dates, and stages are preserved; actual vs
  forecast, current vs prospective, signed vs discussed, approved vs planned, and TAM vs SAM
  vs SOM stay distinct.
- Excluded by default: investor identities, committed capital, undisclosed negotiations, deal
  terms and valuation, proprietary technical details, detailed IP strategy, personal contact
  details, customer identities.
- Approval required before export: the funding ask, content from slides marked confidential,
  unsupported claims, and figures not found on their cited pages.
- `committed_capital` items never appear in the funding section unless explicitly approved.
