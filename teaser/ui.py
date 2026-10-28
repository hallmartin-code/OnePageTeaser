"""TEN Capital "One Page Teaser" look for the Streamlit app.

Colours and fonts for native widgets come from ``.streamlit/config.toml``. This module adds
what a theme cannot express: the tri-colour ambient glow, the brand lockup, the hero card
with its gradient hairline, the dashed dropzone, the gradient primary button, the step
strip, and the disclosure. Styling targets Streamlit's stable ``data-testid`` attributes and
the ``st-key-<key>`` classes that keyed containers receive.
"""

from __future__ import annotations

import base64
import functools
from html import escape

import streamlit as st

from teaser.config import PROJECT_ROOT, Settings

NAVY_950, NAVY_900, NAVY_800, NAVY_700 = "#0B1526", "#101E33", "#16283F", "#1E354F"
CORAL, CORAL_SOFT, AMBER, TEAL = "#EE5A4E", "#F0776C", "#F3A22A", "#35BEBB"
INK_100, INK_300, INK_500, INK_600 = "#F3F6FA", "#C4D0E0", "#7E90A8", "#5C6E86"

STEPS = ("Upload", "Draft", "Review", "Export")

_CSS = f"""
<style>
.stApp {{
  background:
    radial-gradient(480px 380px at 14% 8%, rgba(238,90,78,0.16), transparent 60%),
    radial-gradient(480px 380px at 86% 6%, rgba(243,162,42,0.13), transparent 60%),
    radial-gradient(560px 420px at 50% 100%, rgba(53,190,187,0.12), transparent 60%),
    {NAVY_950};
  background-attachment: fixed;
}}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.2rem; }}

/* brand lockup */
.ten-brand {{ display:flex; align-items:center; gap:12px; margin: 0 0 22px 4px; }}
.ten-brand img {{ width:34px; height:34px; }}
.ten-brand-word {{ font-family:'Sora',sans-serif; font-weight:800; font-size:15px; letter-spacing:.04em;
  line-height:1.15; text-transform:uppercase; color:{INK_100}; }}
.ten-brand-word span {{ display:block; font-weight:600; font-size:10px; letter-spacing:.22em; color:{INK_500}; margin-top:2px; }}

/* hero card: any keyed container whose key starts with "hero" */
[class*="st-key-hero"] {{
  background: linear-gradient(180deg, {NAVY_900} 0%, {NAVY_800} 100%);
  border: 1px solid {NAVY_700}; border-radius: 20px;
  padding: 40px 40px 30px;
  box-shadow: 0 30px 60px -20px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.03);
  position: relative; overflow: hidden;
}}
[class*="st-key-hero"]::after {{
  content:""; position:absolute; top:0; left:40px; right:40px; height:2px; border-radius:2px;
  background: linear-gradient(90deg, {CORAL}, {AMBER}, {TEAL});
}}
@media (max-width: 640px) {{ [class*="st-key-hero"] {{ padding: 28px 20px 22px; }} }}

.ten-eyebrow {{ display:flex; align-items:center; gap:8px; font-family:'JetBrains Mono',monospace; font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; color:{TEAL}; margin-bottom:12px; }}
.ten-eyebrow::before {{ content:""; width:6px; height:6px; border-radius:50%; background:{TEAL};
  box-shadow:0 0 0 3px rgba(53,190,187,.18); }}
.ten-h1 {{ font-family:'Sora',sans-serif; font-size:28px; font-weight:700; line-height:1.25; margin:0 0 10px;
  letter-spacing:-.01em; color:{INK_100}; }}
.ten-h1 .nw {{ white-space:nowrap; }}
.ten-h1 .arrow {{ color:{INK_500}; font-weight:400; margin:0 4px; }}
.ten-h1 .to {{ white-space:nowrap; background: linear-gradient(90deg, {CORAL_SOFT}, {AMBER}); -webkit-background-clip:text;
  background-clip:text; color:transparent; }}
.ten-lede {{ color:{INK_300}; font-size:15px; line-height:1.6; margin:0 0 18px; max-width:52ch; }}

/* step strip */
.ten-steps {{ display:flex; gap:6px; margin: 4px 0 22px; flex-wrap: wrap; }}
.ten-step {{ font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
  padding:5px 10px; border-radius:999px; border:1px solid {NAVY_700}; color:{INK_500}; }}
.ten-step.done {{ color:{TEAL}; border-color:rgba(53,190,187,.35); }}
.ten-step.current {{ color:#17130E; border-color:transparent;
  background: linear-gradient(90deg, {CORAL}, {AMBER}); font-weight:500; }}

/* dropzone */
[data-testid="stFileUploaderDropzone"] {{
  border: 1.5px dashed {NAVY_700}; border-radius: 14px; background: rgba(255,255,255,.015);
  padding: 30px 24px; transition: border-color .18s ease, background .18s ease;
}}
[data-testid="stFileUploaderDropzone"]:hover {{ border-color:{TEAL}; background: rgba(53,190,187,.05); }}

/* gradient primary buttons (Generate, Download PDF) */
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {{
  background: linear-gradient(90deg, {CORAL} 0%, {CORAL_SOFT} 45%, {AMBER} 100%);
  color: #17130E; border: none; font-family:'Sora',sans-serif; font-weight:700; letter-spacing:.01em;
  padding: 0.7rem 1.2rem; box-shadow: 0 10px 24px -10px rgba(238,90,78,.45);
  transition: filter .15s ease, transform .15s ease;
}}
button[data-testid="stBaseButton-primary"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover {{
  filter: brightness(1.06); transform: translateY(-1px); color:#17130E; border:none;
}}
button[data-testid="stBaseButton-primary"] p, button[data-testid="stBaseButton-primaryFormSubmit"] p {{ font-weight:700; }}
button[data-testid="stBaseButton-primary"]:disabled {{ filter: grayscale(.7) brightness(.6); transform:none; box-shadow:none; }}

/* section cards below the hero */
[data-testid="stVerticalBlockBorderWrapper"] {{ background: rgba(16,30,51,.55); }}

.ten-disclosure {{ margin-top: 18px; padding-top: 16px; border-top: 1px solid {NAVY_700}; font-size:12px;
  line-height:1.6; color:{INK_500}; }}
.ten-disclosure code {{ font-family:'JetBrains Mono',monospace; background:{NAVY_950}; border:1px solid {NAVY_700};
  color:{INK_300}; padding:2px 6px; border-radius:5px; font-size:11.5px; }}
.ten-section-title {{ font-family:'Sora',sans-serif; font-weight:700; font-size:20px; color:{INK_100}; margin: 6px 0 2px; }}
.ten-section-sub {{ color:{INK_500}; font-size:13px; margin-bottom: 10px; }}
.ten-footer {{ text-align:center; margin: 36px 0 8px; font-family:'JetBrains Mono',monospace; font-size:11px;
  letter-spacing:.08em; color:{INK_600}; text-transform:uppercase; }}
</style>
"""


@functools.cache
def _mark_data_uri() -> str:
    data = (PROJECT_ROOT / "static" / "favicon.png").read_bytes()
    return "data:image/png;base64," + base64.standard_b64encode(data).decode("ascii")


def inject_css() -> None:
    """Add the stylesheet. A style-only st.html takes no layout space."""
    st.html(_CSS)


def brand() -> None:
    """TEN Capital Network lockup."""
    st.html(
        f'<div class="ten-brand"><img src="{_mark_data_uri()}" alt="TEN Capital Network logo">'
        f'<div class="ten-brand-word">Ten Capital<span>Network</span></div></div>'
    )


def hero_intro(current_step: int) -> None:
    """Eyebrow, headline, lede, and the four-step strip (0-based current step)."""
    steps = "".join(
        f'<span class="ten-step {"done" if i < current_step else "current" if i == current_step else ""}">'
        f"{i + 1} · {name}</span>"
        for i, name in enumerate(STEPS)
    )
    st.html(
        '<div class="ten-eyebrow">One Page Teaser</div>'
        '<div class="ten-h1"><span class="nw">Pitch Deck</span><span class="arrow">&rarr;</span>'
        '<span class="to">Investor One&#8209;Pager</span></div>'
        '<p class="ten-lede">Upload a pitch deck and get a polished single-page investor PDF, '
        "analyzed and structured by Claude, then checked against the deck and screened for "
        "confidential information before you approve it.</p>"
        f'<div class="ten-steps">{steps}</div>'
    )


def section_title(title: str, subtitle: str = "") -> None:
    """Heading for the work areas below the hero card."""
    sub = f'<div class="ten-section-sub">{escape(subtitle)}</div>' if subtitle else ""
    st.html(f'<div class="ten-section-title">{escape(title)}</div>{sub}')


def disclosure(settings: Settings) -> None:
    """What happens to the uploaded file, stated from the live configuration."""
    parts = ["The deck is processed in memory on this server and is not stored."]
    if settings.sends_externally:
        parts.append(
            f"When you generate with the model, its extracted text is sent to the "
            f"{escape(settings.provider_label)} (<code>{escape(settings.model)}</code>)."
        )
    if settings.email_enabled:
        recipients = ", ".join(f"<code>{escape(r)}</code>" for r in settings.email_recipients)
        parts.append(f"A summary and the teaser PDF of every generation are emailed to {recipients}.")
    else:
        parts.append("Result emails are off.")
    st.html(f'<div class="ten-disclosure">{" ".join(parts)}</div>')


def footer() -> None:
    st.html('<div class="ten-footer">Powered by TEN Capital Network</div>')
