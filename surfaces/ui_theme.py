"""ui_theme — the visual identity for the Streamlit Trip Planner.

One place for the palette + CSS so the cascade stays clean. Design:
  ground  #F6F3EE  (warm off-white sand)
  text    #2A2622  (warm near-black)
  accent  #E07A3F  (terracotta — Dubai desert/sunset)
  accent2 #1F6F6B  (deep teal — oasis water; keeps it from being generic cream)
Type: Fraunces (display serif) + Inter (UI/body). Floating glass navbar.
"""

from __future__ import annotations

import streamlit as st

PALETTE = {
    "ground": "#F6F3EE",
    "surface": "#FBF9F5",
    "text": "#2A2622",
    "muted": "#7A726A",
    "accent": "#E07A3F",
    "accent2": "#1F6F6B",
    "line": "#E7E0D6",
}

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap');

:root {
  --ground:  #F6F3EE;
  --surface: #FBF9F5;
  --text:    #2A2622;
  --muted:   #7A726A;
  --accent:  #E07A3F;
  --accent2: #1F6F6B;
  --line:    #E7E0D6;
  --radius:  18px;
  --shadow:  0 10px 30px -12px rgba(42,38,34,.18);
}

/* ---- base ---- */
html, body, [class*="css"], .stApp {
  background: var(--ground);
  color: var(--text);
  font-family: 'Inter', system-ui, sans-serif;
}
.stApp {
  /* soft desert-to-oasis wash so the off-white isn't flat */
  background:
    radial-gradient(1200px 480px at 88% -8%, rgba(224,122,63,.08), transparent 60%),
    radial-gradient(900px 420px at 0% 8%, rgba(31,111,107,.06), transparent 55%),
    var(--ground);
}

/* headings in the display serif */
h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
  font-family: 'Fraunces', Georgia, serif !important;
  letter-spacing: -0.01em;
  color: var(--text);
}
h1 { font-weight: 600; }

/* push content below the floating navbar */
.block-container { padding-top: 5.4rem !important; max-width: 1180px; }

/* ---- floating glassmorphism navbar ---- */
/* Streamlit's st.tabs becomes the nav: pin it, frost it, pill it. */
div[data-baseweb="tab-list"] {
  position: fixed;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1000;
  gap: 6px;
  padding: 7px;
  background: rgba(251,249,245,.65);
  backdrop-filter: blur(16px) saturate(150%);
  -webkit-backdrop-filter: blur(16px) saturate(150%);
  border: 1px solid rgba(255,255,255,.55);
  border-radius: 999px;
  box-shadow: 0 8px 28px -10px rgba(42,38,34,.28);
}
div[data-baseweb="tab-list"] button[data-baseweb="tab"] {
  border-radius: 999px !important;
  padding: 8px 20px !important;
  font-family: 'Inter', sans-serif;
  font-weight: 600;
  font-size: 0.92rem;
  color: var(--muted);
  background: transparent;
  transition: all .18s ease;
}
div[data-baseweb="tab-list"] button[data-baseweb="tab"]:hover {
  color: var(--text);
  background: rgba(224,122,63,.10);
}
div[data-baseweb="tab-list"] button[aria-selected="true"] {
  color: #fff !important;
  background: var(--accent) !important;
  box-shadow: 0 4px 12px -3px rgba(224,122,63,.5);
}
/* hide the default underline highlight bar */
div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] { display: none !important; }

/* ---- cards (st.container(border=True)) ---- */
div[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--surface);
  border: 1px solid var(--line) !important;
  border-radius: var(--radius) !important;
  box-shadow: var(--shadow);
  overflow: hidden;
}

/* images sit flush + rounded inside cards */
div[data-testid="stImage"] img { border-radius: 12px; }

/* primary buttons */
.stButton button[kind="primary"], .stButton button[data-testid="baseButton-primary"] {
  background: var(--accent);
  border: none;
  border-radius: 999px;
  font-weight: 600;
  box-shadow: 0 6px 16px -6px rgba(224,122,63,.5);
}
.stButton button { border-radius: 999px; }

/* chat bubbles a touch warmer */
div[data-testid="stChatMessage"] { background: transparent; }

/* sidebar */
section[data-testid="stSidebar"] {
  background: var(--surface);
  border-right: 1px solid var(--line);
}

/* metrics */
div[data-testid="stMetric"] {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 12px 16px;
}

/* dataframe corners */
div[data-testid="stDataFrame"] { border-radius: 14px; overflow: hidden; }
</style>
"""


def inject_theme() -> None:
    """Apply the off-white / glass-navbar identity. Call once after set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)


# ---- timeline + image-card helpers (used by streamlit_app) -------------------
_CATEGORY_TINT = {
    "flight": ("#E07A3F", "✈️"),
    "hotel": ("#1F6F6B", "🏨"),
    "tour": ("#C2683A", "🎟️"),
    "transfer": ("#5B6B7A", "🚐"),
    "restaurant": ("#B5562E", "🍽️"),
    "meal": ("#B5562E", "🍽️"),
    "visa": ("#4A7C59", "📄"),
    "other": ("#7A726A", "📍"),
}


def placeholder_tile_html(label: str, kind: str = "hotel", sub: str = "") -> str:
    """A gradient placeholder tile for items with no real image (e.g. hotels —
    the supplier API has no hotel photos, so we never fake one)."""
    tint, glyph = _CATEGORY_TINT.get(kind, _CATEGORY_TINT["other"])
    sub_html = f"<div style='font-size:.72rem;opacity:.85;margin-top:2px'>{sub}</div>" if sub else ""
    return f"""
    <div style="
        height:150px;border-radius:12px;display:flex;flex-direction:column;
        align-items:center;justify-content:center;color:#fff;text-align:center;
        font-family:'Inter',sans-serif;padding:10px;
        background:linear-gradient(135deg,{tint} 0%, #2A2622 160%);">
      <div style="font-size:2rem;line-height:1">{glyph}</div>
      <div style="font-weight:600;font-size:.9rem;margin-top:6px;max-width:90%">{label}</div>
      {sub_html}
    </div>"""
