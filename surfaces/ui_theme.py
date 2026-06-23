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

/* Hide Streamlit's own top toolbar/header so our floating navbar owns the top.
   (The header is a translucent bar that otherwise overlaps the nav pill.) */
header[data-testid="stHeader"] { background: transparent !important; height: 0 !important; }
header[data-testid="stHeader"] > * { display: none !important; }
div[data-testid="stToolbar"] { display: none !important; }

/* push content below the floating navbar */
.block-container { padding-top: 5.8rem !important; max-width: 1180px; }

/* ---- floating glassmorphism navbar ---- */
/* Streamlit's st.tabs becomes the nav: pin it, frost it, pill it. */
div[data-baseweb="tab-list"] {
  position: fixed;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 999999;
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


def _to_minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def render_calendar_html(days: list[dict], *, start_hour: int = 8, end_hour: int = 23) -> str:
    """Build a time-grid calendar (time rows × day columns) from build_trip_schedule
    output. Mirrors the inspiration: each item is a colored block positioned by its
    start/end time. Returns a self-contained HTML string for components.html."""
    if not days:
        return "<p>No schedule yet.</p>"
    row_h = 56  # px per hour
    grid_h = (end_hour - start_hour) * row_h
    ncols = len(days)

    # time gutter labels
    gutter = "".join(
        f"<div style='height:{row_h}px;font:500 11px Inter,sans-serif;color:{PALETTE['muted']};"
        f"text-align:right;padding-right:8px;transform:translateY(-7px)'>"
        f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}</div>"
        for h in range(start_hour, end_hour)
    )

    cols_html = ""
    for d in days:
        # header
        date = d.get("date", "")
        label = d.get("label", "")
        head = (
            f"<div style='text-align:center;padding:10px 6px;border-bottom:1px solid {PALETTE['line']}'>"
            f"<div style='font:600 13px Inter,sans-serif;color:{PALETTE['text']}'>{date}</div>"
            f"<div style='font:500 11px Inter,sans-serif;color:{PALETTE['muted']}'>{label}</div></div>"
        )
        # blocks
        blocks = ""
        for it in d.get("items", []):
            tint, glyph = _CATEGORY_TINT.get(it.get("kind", "other"), _CATEGORY_TINT["other"])
            s = _to_minutes(it.get("start", "")) or (start_hour * 60)
            e = _to_minutes(it.get("end", "")) or (s + 60)
            top = max(0, (s - start_hour * 60) / 60 * row_h)
            height = max(34, (e - s) / 60 * row_h - 4)
            time_lbl = it.get("start", "")
            detail = (
                f"<div style='font:400 10px Inter;opacity:.85;margin-top:1px;"
                f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{it['detail']}</div>"
                if it.get("detail") else ""
            )
            blocks += (
                f"<div style='position:absolute;top:{top}px;left:4px;right:4px;height:{height}px;"
                f"background:{tint};color:#fff;border-radius:10px;padding:6px 8px;overflow:hidden;"
                f"box-shadow:0 4px 10px -4px rgba(0,0,0,.3)'>"
                f"<div style='font:600 11px Inter;line-height:1.15'>{glyph} {it['title']}</div>"
                f"<div style='font:500 9px Inter;opacity:.9'>{time_lbl}</div>{detail}</div>"
            )
        cols_html += (
            f"<div style='flex:1;min-width:150px;border-left:1px solid {PALETTE['line']}'>"
            f"{head}"
            f"<div style='position:relative;height:{grid_h}px;background:"
            f"repeating-linear-gradient(to bottom,transparent,transparent {row_h - 1}px,{PALETTE['line']} {row_h}px)'>"
            f"{blocks}</div></div>"
        )

    return f"""
    <div style="font-family:Inter,sans-serif;background:{PALETTE['surface']};
         border:1px solid {PALETTE['line']};border-radius:16px;overflow:hidden;
         box-shadow:0 10px 30px -12px rgba(42,38,34,.18)">
      <div style="display:flex">
        <div style="width:58px;flex:none;padding-top:{44}px">{gutter}</div>
        <div style="display:flex;flex:1;overflow-x:auto">{cols_html}</div>
      </div>
    </div>"""


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
