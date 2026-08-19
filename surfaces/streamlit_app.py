"""Streamlit web UI — single-file surface.

Run:
    streamlit run surfaces/streamlit_app.py

Contains:
- Session state setup
- Sidebar (quickstart form + budget tracker + debug log)
- Inline card renderers (flight, hotel, tour, transfer, restaurant, visa)
- Chat handler with card-display detection
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from agent import (
    StreamResult,
    build_in_memory_checkpoint,
    build_react_agent,
    configure_logging,
    extract_search_options,
    extract_tool_calls,
    stream_and_log,
)
from booking_api.http_client import http_requests_since, latest_http_seq
from core import format_inr
from itinerary_store import get_itinerary_path
from llm import describe_current_provider

# =============================================================================
# Setup
# =============================================================================
configure_logging(prod=False)
st.set_page_config(page_title="Dubai Trip Planner", page_icon="🏖️", layout="wide")

# Visual identity: off-white "desert sand + oasis" theme, glass floating navbar,
# Fraunces/Inter type, soft image-rich cards. (surfaces/ui_theme.py)
try:
    from surfaces.ui_theme import inject_theme, placeholder_tile_html
except ImportError:  # when run as `streamlit run surfaces/streamlit_app.py`
    from ui_theme import inject_theme, placeholder_tile_html
inject_theme()


def _prompt_fingerprint() -> float:
    """Newest mtime across the prompt files the agent bakes in at build time.

    The agent object lives in st.session_state, so an open browser tab keeps
    using the agent — and therefore the SYSTEM PROMPT — it was built with, even
    after the server restarts. Editing a prompt and reloading the page looked
    like the fix hadn't applied. Rebuild when a prompt file changes.
    """
    root = Path(__file__).resolve().parent.parent / "prompts"
    try:
        return max(p.stat().st_mtime for p in root.glob("*.md"))
    except ValueError:
        return 0.0


def _init_session() -> None:
    fingerprint = _prompt_fingerprint()
    if "agent" not in st.session_state or st.session_state.get("_prompt_fp") != fingerprint:
        st.session_state.agent = build_react_agent(
            surface="streamlit", checkpoint_store=build_in_memory_checkpoint()
        )
        st.session_state._prompt_fp = fingerprint
    st.session_state.setdefault("thread_id", f"web_{uuid.uuid4().hex[:12]}")
    st.session_state.setdefault("chat_history", [])
    # Full debug records: one dict per tool call with turn, name, input, output.
    st.session_state.setdefault("debug_calls", [])
    # Real HTTP requests (method + full URL + status) the supplier client made,
    # tagged by turn — the ground truth for "which API was actually called".
    st.session_state.setdefault("http_calls", [])
    # Per-turn metadata: {turn, user_msg, n_tool_calls, n_api, quoted_price}.
    # Lets the inspector flag "this answer reused numbers — no new API call",
    # distinguishing legitimate reuse from hallucination.
    st.session_state.setdefault("turn_log", [])
    # Id of the most recently generated itinerary PDF (for the download button).
    st.session_state.setdefault("itinerary_id", None)
    # Set when the user clicks "Generate PDF" so the next run asks the agent.
    st.session_state.setdefault("pdf_request_pending", False)
    # Last search options rendered as cards ({kind, options}), so a follow-up
    # like "show me the images" can re-display them without a fresh search.
    st.session_state.setdefault("last_search", {})
    st.session_state.setdefault("turn_number", 0)


# =============================================================================
# Card detection
# =============================================================================
# MULTILINE matters: the trigger phrase almost never starts the message. A
# "plan everything" reply opens with a lead-in or a section heading and puts
# "Here are the top 3 flights:" partway down, so a `^` anchored to position 0
# matched nothing and the richest turn in the product rendered zero cards.
CARD_TRIGGER_RE = re.compile(
    r"^[ \t]*\**\s*here\s+are\s+the\s+top\s+\d+\s+"
    r"(flights?|hotels?|tours?|transfers?|restaurants?|visa\s+options?)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
USER_DISPLAY_KEYWORDS = (
    "show me",
    "list",
    "compare",
    "any options",
    "what are the options",
    "render",
    "show the image",
    "show image",
    "show pic",
    "show photo",
    "with image",
    "see the image",
)


def _should_render_cards(user_text: str, agent_text: str) -> bool:
    if CARD_TRIGGER_RE.search(agent_text or ""):
        return True
    t = (user_text or "").lower()
    return any(kw in t for kw in USER_DISPLAY_KEYWORDS)


def _display_signal(tool_calls: list[dict[str, Any]]) -> str | None:
    """If the agent called display_options_tool this turn, return the requested
    kind (e.g. 'tour'). This is the explicit, production path for showing cards —
    the keyword/regex detection above is only a fallback."""
    for tc in tool_calls:
        if tc.get("tool_name") == "display_options_tool":
            out = _coerce_output(tc.get("output"))
            if isinstance(out, dict) and out.get("display") and out.get("kind"):
                return str(out["kind"])
    return None


def _schedule_signal(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """If the agent called build_trip_schedule_tool this turn, return its days so
    the chat can render the calendar grid."""
    for tc in tool_calls:
        if tc.get("tool_name") == "build_trip_schedule_tool":
            out = _coerce_output(tc.get("output"))
            if isinstance(out, dict) and out.get("schedule") and out.get("days"):
                return out["days"]
    return None


# A rupee amount with at least 3 digits (e.g. ₹2,83,844 or ₹104934) — used to
# detect when an answer quotes a concrete price so we can flag answers that
# state prices without having called any pricing tool that turn.
PRICE_RE = re.compile(r"₹\s?\d[\d,]{2,}")


def _mentions_price(text: str) -> bool:
    return bool(PRICE_RE.search(text or ""))


# =============================================================================
# Card renderers (one function per kind)
# =============================================================================
def _render_flight(o: dict[str, Any]) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**✈️ {o.get('airline') or 'Flight'}**")
            if o.get("route_outbound"):
                st.caption(f"Outbound: {o['route_outbound']}")
            if o.get("route_return"):
                st.caption(f"Return: {o['route_return']}")
            stops = o.get("stops", 0)
            duration = o.get("duration_min", 0)
            stops_label = "Non-stop" if stops == 0 else f"{stops} stop(s)"
            dur = f"{duration // 60}h {duration % 60}m" if duration else "—"
            st.caption(f"{stops_label} · {dur}")
            if o.get("refundable"):
                st.caption("✓ Refundable")
            if o.get("baggage_info"):
                st.caption(f"🧳 {', '.join(o['baggage_info'])}")
        with c2:
            st.markdown(f"### {format_inr(o.get('price_inr', 0))}")
            st.caption("Total (all pax)")


def _render_calendar(days: list[dict[str, Any]]) -> None:
    """Render the day-by-day schedule as a calendar time-grid via a popover."""
    import streamlit.components.v1 as components

    try:
        from surfaces.ui_theme import render_calendar_html
    except ImportError:
        from ui_theme import render_calendar_html

    start_hour = 8
    end_hour = 23
    # height: header (~50) + hours * 64 + padding
    cal_height = (end_hour - start_hour) * 64 + 100

    with st.popover("📅 View trip schedule →", use_container_width=True):
        components.html(render_calendar_html(days), height=cal_height, scrolling=False)


def _render_timestamp(ts: str | None) -> None:
    """Render a small, light-grey timestamp above a chat message."""
    if not ts:
        return
    st.markdown(
        f"<div style='font-size:0.72rem;color:#9a9a9a;margin-bottom:2px;'>{ts}</div>",
        unsafe_allow_html=True,
    )


def _now_stamp() -> str:
    """Human-friendly IST timestamp for chat messages, e.g. '13 Jul, 12:27 PM'.

    Uses Asia/Kolkata explicitly — the deployed server runs in UTC, so a plain
    datetime.now() showed times ~5.5h behind for Indian users.
    """
    from datetime import timezone, timedelta
    ist = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    fmt = "%-d %b, %-I:%M %p" if _supports_dash() else "%d %b, %I:%M %p"
    return ist.strftime(fmt)


def _supports_dash() -> bool:
    """strftime %-d works on Unix, not Windows. Detect once."""
    try:
        datetime.now().strftime("%-d")
        return True
    except ValueError:
        return False


def _render_hotel(o: dict[str, Any]) -> None:
    with st.container(border=True):
        name = o.get("hotel_name") or "Hotel"
        stars = int(o.get("stars", 0) or 0)
        img_col, body_col = st.columns([1, 2])
        with img_col:
            # Use real hotel image if available, else gradient placeholder
            img_urls = o.get("image_urls") or []
            first_img = img_urls[0] if img_urls else None
            if first_img:
                try:
                    # Prepend base URL if relative path
                    if not first_img.startswith("http"):
                        first_img = "https://stagingapi.gujjutours.com/" + first_img.lstrip("/")
                    st.image(first_img, width=220)
                except Exception:
                    st.markdown(
                        placeholder_tile_html(name, kind="hotel", sub="⭐" * stars if stars else ""),
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    placeholder_tile_html(name, kind="hotel", sub="⭐" * stars if stars else ""),
                    unsafe_allow_html=True,
                )
        with body_col:
            c1, c2 = st.columns([3, 1])
            with c1:
                star_str = "⭐" * stars
                st.markdown(f"**🏨 {name}** {star_str}")
                if o.get("amenities_matched"):
                    st.caption("✓ " + " · ".join(o["amenities_matched"]))
                if o.get("area"):
                    st.caption(f"📍 {o['area']}")
                if o.get("cheapest_room_type"):
                    st.caption(f"Room: {o['cheapest_room_type']}")
                if o.get("cheapest_board"):
                    st.caption(f"Board: {o['cheapest_board']}")
                if o.get("has_free_cancellation"):
                    st.caption("✓ Free cancellation")
            with c2:
                st.markdown(f"### {format_inr(o.get('price_inr', 0))}")
                nights = o.get("nights", 0)
                st.caption(f"{nights} nights · {format_inr(o.get('per_night_inr', 0))}/night")


def _render_tour(o: dict[str, Any]) -> None:
    with st.container(border=True):
        if o.get("image_url"):
            try:
                # Fixed small width — NOT use_container_width (that stretches to
                # the full card and overrides width, making the image huge).
                st.image(o["image_url"], width=300)
            except Exception:
                pass
        c1, c2 = st.columns([3, 1])
        with c1:
            badge = " ⭐ Recommended" if o.get("is_recommended") else ""
            st.markdown(f"**🎟️ {o.get('name', 'Tour')}**{badge}")
            cat = o.get("category", "")
            dur = o.get("duration", "")
            if cat or dur:
                st.caption(f"{cat} · {dur}".strip(" ·"))
            if o.get("rating"):
                st.caption(f"⭐ {o['rating']:.1f} ({o.get('reviews_count', 0)} reviews)")
            desc = o.get("short_description") or ""
            if desc:
                st.write(desc[:200] + ("…" if len(desc) > 200 else ""))
        with c2:
            st.markdown(f"### {format_inr(o.get('price_per_adult_inr', 0))}")
            st.caption("per adult")


def _render_transfer(o: dict[str, Any]) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**🚐 {o.get('vehicle_name', 'Vehicle')}**")
            if o.get("transfer_type"):
                st.caption(o["transfer_type"])
            cap = o.get("capacity", 0)
            lug = o.get("luggage_capacity", 0)
            if cap:
                st.caption(f"👥 Up to {cap} pax · 🧳 {lug} bags")
            if o.get("estimated_time"):
                st.caption(f"⏱️ {o['estimated_time']}")
            badges = o.get("badges") or []
            if badges:
                st.caption(" · ".join(f"`{b}`" for b in badges))
        with c2:
            st.markdown(f"### {format_inr(o.get('price_inr', 0))}")
            st.caption("total")


def _render_restaurant(o: dict[str, Any]) -> None:
    with st.container(border=True):
        # Show image if available
        img_url = o.get("image_url") or (o.get("image_urls") or [None])[0]
        if img_url:
            if not img_url.startswith("http"):
                img_url = "https://stagingapi.gujjutours.com/" + img_url.lstrip("/")
            try:
                st.image(img_url, width=300)
            except Exception:
                pass
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**🍽️ {o.get('name', 'Restaurant')}**")
            chips = [x for x in (o.get("cuisine"), o.get("veg_type")) if x]
            if chips:
                st.caption(" · ".join(chips))
            if o.get("city"):
                st.caption(f"📍 {o['city']}")
            if o.get("rating"):
                st.caption(f"⭐ {o['rating']:.1f}")
            opening = o.get("opening_time")
            closing = o.get("closing_time")
            if opening and closing:
                st.caption(f"⏰ {opening} – {closing}")
        with c2:
            st.markdown(f"### {format_inr(o.get('price_per_adult_inr', 0))}")
            st.caption("per person")


def _render_visa(o: dict[str, Any]) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**📄 {o.get('visa_type', 'Tourist Visa')}**")
            chips: list[str] = []
            if o.get("entry_type"):
                chips.append(o["entry_type"])
            if o.get("is_evisa"):
                chips.append("eVisa")
            if chips:
                st.caption(" · ".join(chips))
            if o.get("validity"):
                st.caption(f"Valid: {o['validity']}")
            if o.get("stay_duration"):
                st.caption(f"Max stay: {o['stay_duration']}")
            if o.get("processing_days"):
                st.caption(f"⏱️ Processing: ~{o['processing_days']} working days")
        with c2:
            pricing_available = o.get("pricing_available", False)
            if not pricing_available or o.get("price_per_person_inr", 0) == 0:
                st.markdown("### On Request")
                st.caption("contact agency")
            else:
                st.markdown(f"### {format_inr(o['price_per_person_inr'])}")
                st.caption("per person")


_RENDERERS = {
    "flight": _render_flight,
    "hotel": _render_hotel,
    "tour": _render_tour,
    "transfer": _render_transfer,
    "restaurant": _render_restaurant,
    "visa": _render_visa,
}


def _render_option(kind: str, option: dict[str, Any]) -> None:
    fn = _RENDERERS.get(kind)
    if fn is not None:
        fn(option)


# =============================================================================
# Debug inspector — every tool/API call with its inputs and outputs
# =============================================================================
# Which tools hit a live supplier HTTP endpoint (vs. local compute-only tools).
# Lets the inspector label each call as a real API hit so you can tell at a
# glance whether a number came from the network or from the agent's head.
_API_BACKED_TOOLS = {
    "search_flights",
    "search_hotels",
    "search_tours",
    "search_airport_transfer_dubai",  # registered name; "search_transfers" never existed
    "search_restaurants",
    "get_visa_info",
    "list_packages",
    "get_flight_details",
    "get_tour_details",
    "get_transfer_details",
    "get_restaurant_details",
    "get_package_details",
    "lookup_hotel_city",
    "list_city_hotels",
    "get_hotel_info",
    "get_hotel_description",
    "get_hotel_reviews",
    "get_exchange_rate",
}


def _call_status(call: dict[str, Any]) -> tuple[str, str]:
    """Return (emoji, short_label) describing a call's outcome.

    Errors and empty result sets are the usual precursors to a hallucination
    (the model invents data when the tool gave it nothing), so they're flagged
    loudly here.
    """
    output = _coerce_output(call.get("output"))
    if isinstance(output, dict):
        if output.get("error"):
            return "🔴", f"ERROR: {output.get('error_type', 'unknown')}"
        opts = output.get("options")
        if isinstance(opts, list):
            return ("🟢", f"{len(opts)} options") if opts else ("🟡", "0 options")
        if "source" in output and "rate" in output:
            src = output.get("source")
            return ("🟢" if src == "live_api" else "🟡", f"{src}")
    return "🟢", "ok"


def _status_code_emoji(status_code: int | None, error: str | None) -> str:
    if error:
        return "🔴"
    if status_code is None:
        return "⚪"
    if 200 <= status_code < 300:
        return "🟢"
    if 400 <= status_code < 600:
        return "🔴"
    return "🟡"


def _render_call_record(call: dict[str, Any], idx: int) -> None:
    """Render one tool call: name, status, the actual API URL, inputs, output."""
    emoji, label = _call_status(call)
    name = call.get("tool_name", "?")
    api_badge = "🌐 API" if name in _API_BACKED_TOOLS else "⚙️ local"
    with st.expander(f"{emoji} `{name}` · {label}", expanded=False):
        st.caption(f"{api_badge} · turn {call.get('turn', '?')}")

        # The actual HTTP endpoint that was hit — the ground truth for "which API".
        http = call.get("http")
        if http:
            sc = http.get("status_code")
            sc_emoji = _status_code_emoji(sc, http.get("error"))
            st.markdown("**Actual API call:**")
            st.code(f"{http.get('method', '?')} {http.get('url', '?')}", language="http")
            status_txt = http.get("error") or (f"HTTP {sc}" if sc is not None else "no status")
            st.caption(f"{sc_emoji} {status_txt} · {http.get('duration_ms', '?')} ms")
        elif name in _API_BACKED_TOOLS:
            st.warning(
                "No HTTP request was recorded for this API-backed tool — it may "
                "have used cached data, or the call never reached the network.",
                icon="⚠️",
            )
        else:
            st.caption("⚙️ Local tool — no network request (pure computation).")

        st.markdown("**Input (args the agent passed):**")
        agent_input = call.get("input") or {}
        if agent_input:
            st.json(agent_input, expanded=True)
        else:
            st.caption("_(no arguments)_")

        st.markdown("**Output (what the tool returned):**")
        _render_output(call.get("output"), key=f"out_{call.get('turn', 0)}_{idx}")


def _output_summary(output: dict[str, Any]) -> list[str]:
    """One-line highlights for a (possibly huge) tool output, so the key facts
    are visible without expanding the full JSON."""
    lines: list[str] = []
    if output.get("error"):
        lines.append(f"❌ error: {output.get('message') or output.get('error_type')}")
        return lines
    opts = output.get("options")
    if isinstance(opts, list):
        lines.append(f"{len(opts)} option(s) returned")
        if opts and isinstance(opts[0], dict):
            first = opts[0]
            name = first.get("airline") or first.get("hotel_name") or first.get("name") or "—"
            price = (
                first.get("price_inr")
                or first.get("price_per_adult_inr")
                or first.get("price_total_inr")
            )
            lines.append(f"cheapest: {name}" + (f" · ₹{price:,.0f}" if price else ""))
    for k in ("rate", "source", "total_results", "status"):
        if k in output:
            lines.append(f"{k}: {output[k]}")
    return lines


def _render_output(output: Any, *, key: str) -> None:
    """Render a tool output so even LARGE responses are usable.

    Short/None outputs render inline. Dict/list outputs get a highlights summary
    plus the full JSON in a scrollable, expandable widget, with a Copy/Download
    of the raw JSON so nothing is hidden behind a collapsed `{...}`.
    """
    if output in (None, ""):
        st.caption("_(empty response)_")
        return
    if not isinstance(output, (dict, list)):
        st.code(str(output))
        return

    raw = json.dumps(output, indent=2, ensure_ascii=False, default=str)
    size_kb = len(raw) / 1024
    is_large = size_kb > 2.0

    if isinstance(output, dict):
        for line in _output_summary(output):
            st.caption(f"• {line}")

    # Expand small payloads by default; keep large ones collapsed but explorable.
    st.json(output, expanded=not is_large)
    st.caption(f"{size_kb:.1f} KB · full JSON below if the tree is hard to read")
    # st.code gives a built-in copy button; a text_area is reliably scrollable
    # for very long responses where the JSON tree is awkward in a narrow sidebar.
    with st.popover("📋 View / copy raw JSON", use_container_width=True):
        st.code(raw, language="json")
    st.download_button(
        "⬇️ Download JSON",
        data=raw,
        file_name=f"{key}.json",
        mime="application/json",
        use_container_width=True,
        key=f"dl_{key}",
    )


def _last_api_turn(turn_log: list[dict[str, Any]], before_turn: int) -> int | None:
    """The most recent turn at/before `before_turn` that actually hit an API."""
    best: int | None = None
    for t in turn_log:
        if t.get("turn", 0) <= before_turn and t.get("n_api", 0) > 0:
            best = t["turn"]
    return best


def _render_turn_group(turn: int, meta: dict[str, Any] | None, calls: list[dict[str, Any]]) -> None:
    """Render one turn's tool calls, with a reuse banner when it quoted prices
    but called no API — the signal that distinguishes reuse from hallucination."""
    n_api = (meta or {}).get("n_api", 0)
    label = f"Turn {turn}"
    if meta and meta.get("user_msg"):
        label += f" · “{meta['user_msg'][:34]}”"
    st.markdown(f"**{label}**")

    if meta and meta.get("quoted_price") and n_api == 0 and (meta.get("n_tool_calls", 0) == 0):
        anchor = _last_api_turn(st.session_state.get("turn_log", []), turn - 1)
        where = f"from turn {anchor}" if anchor else "from earlier context"
        st.warning(
            f"💬 This answer quoted prices but made **no API/tool call** — the "
            f"numbers were reused {where}, not freshly fetched. Real, but verify "
            f"they're still current.",
            icon="♻️",
        )

    if not calls:
        if not (meta and meta.get("quoted_price")):
            st.caption("_(no tool calls this turn)_")
        return
    for i, call in enumerate(reversed(calls)):
        _render_call_record(call, i)


def _render_debug_inspector() -> None:
    """The debug centerpiece: every API/tool call this session, grouped by turn.

    Shows which tool ran, whether it hit a live API (with the real URL), the
    exact inputs, and the raw output — so every number in a reply traces back to
    real tool data. When a turn quotes prices without calling a tool, a banner
    flags that the figures were reused from an earlier turn (not hallucinated).
    """
    st.subheader("🔎 API / Tool Inspector")
    calls = st.session_state.get("debug_calls", [])
    http_calls = st.session_state.get("http_calls", [])
    turn_log = st.session_state.get("turn_log", [])

    c1, c2, c3 = st.columns(3)
    c1.metric("Tool calls", len(calls))
    c2.metric("API hits", len(http_calls))
    errors = sum(1 for c in calls if _call_status(c)[0] == "🔴")
    errors += sum(1 for h in http_calls if h.get("error") or (h.get("status_code") or 0) >= 400)
    c3.metric("Errors", errors)

    if not calls and not http_calls and not turn_log:
        st.info(
            "No tool calls yet. Every flight/hotel/tour/visa/ROE lookup the "
            "agent makes will appear here with its inputs, the real API URL, "
            "and the raw output."
        )
        return

    cc1, cc2 = st.columns([1, 1])
    if cc1.button("Clear log", use_container_width=True):
        st.session_state.debug_calls = []
        st.session_state.http_calls = []
        st.session_state.turn_log = []
        st.rerun()
    # Default ON: the whole session's history is visible, so reused numbers can
    # always be traced back to the turn whose API call produced them.
    current_only = cc2.toggle("Current turn only", value=False)

    current_turn = st.session_state.get("turn_number", 0)
    meta_by_turn = {t["turn"]: t for t in turn_log}
    turns = sorted({c.get("turn", 0) for c in calls} | {t["turn"] for t in turn_log}, reverse=True)
    if current_only:
        turns = [t for t in turns if t == current_turn]

    for turn in turns:
        _render_turn_group(turn, meta_by_turn.get(turn), [c for c in calls if c.get("turn") == turn])
        st.divider()

    # Raw network trace — every supplier HTTP request, independent of tool mapping.
    visible_http = (
        [h for h in http_calls if h.get("turn") == current_turn] if current_only else http_calls
    )
    if visible_http:
        st.markdown("**🌐 Raw HTTP requests (actual endpoints called):**")
        for h in reversed(visible_http):
            sc = h.get("status_code")
            st.code(f"{h.get('method', '?')} {h.get('url', '?')}", language="http")
            st.caption(
                f"{_status_code_emoji(sc, h.get('error'))} "
                f"{h.get('error') or ('HTTP ' + str(sc) if sc is not None else 'no status')} · "
                f"{h.get('duration_ms', '?')} ms · turn {h.get('turn', '?')}"
            )


# =============================================================================
# Sidebar — debug-only (Quickstart removed; the chat box is the single entry point)
# =============================================================================
def _render_itinerary_section() -> None:
    """Always-available 'Download Itinerary PDF'. Clicking Generate asks the
    agent to build it from the confirmed trip details (real, tool-sourced
    numbers); once built, a download button serves the saved PDF."""
    st.subheader("📄 Itinerary PDF")
    has_chat = bool(st.session_state.get("chat_history"))

    if st.button(
        "🧾 Generate itinerary PDF",
        use_container_width=True,
        disabled=not has_chat,
        help="Builds the branded PDF from the trip details discussed so far.",
    ):
        st.session_state.pdf_request_pending = True
        st.rerun()

    itinerary_id = st.session_state.get("itinerary_id")
    if itinerary_id:
        path = get_itinerary_path(itinerary_id)
        if path is not None:
            st.download_button(
                "⬇️ Download itinerary PDF",
                data=path.read_bytes(),
                file_name="Dubai-itinerary.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary",
            )
            st.caption(f"Ref: {itinerary_id}")
    elif not has_chat:
        st.caption("Plan a trip first, then generate the PDF here.")


# Top models from the conversation stress test (tool accuracy + no-hallucination
# + speed). Label shows the score so you can pick knowingly. Switching rebuilds
# the agent live — no .env edit or restart needed.
_SWITCHABLE_MODELS = {
    "gemma-4-26b": "google/gemma-4-26b-a4b-it",
    "gemma-4-31b": "google/gemma-4-31b-it",
    "deepseek-v3": "deepseek/deepseek-chat-v3-0324",
    "kimi-k2.5": "moonshotai/kimi-k2.5",
}


def _render_model_switcher() -> None:
    """Sidebar dropdown to switch the chat model live (rebuilds the agent)."""
    from llm import get_active_model_id

    current = st.session_state.get("model_override") or get_active_model_id()
    labels = list(_SWITCHABLE_MODELS.keys())
    # Preselect the label matching the current model, else first.
    idx = next((i for i, l in enumerate(labels) if _SWITCHABLE_MODELS[l] == current), 0)

    st.markdown("**🧠 Model** (live switch)")
    choice = st.selectbox(
        "Model", labels, index=idx, label_visibility="collapsed", key="model_choice_label",
    )
    chosen = _SWITCHABLE_MODELS[choice]
    if chosen != st.session_state.get("model_override"):
        st.session_state.model_override = chosen
        # Rebuild the agent with the new model, fresh thread so context doesn't
        # carry a half-turn from the previous model.
        st.session_state.agent = build_react_agent(
            surface="streamlit",
            checkpoint_store=build_in_memory_checkpoint(),
            model_override=chosen,
        )
        st.session_state.thread_id = f"web_{uuid.uuid4().hex[:12]}"
        st.toast(f"Switched to {chosen}")
    st.caption(f"Active: `{chosen}`")


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("🌴 Trip Planner")
        _render_model_switcher()
        st.divider()
        _render_itinerary_section()


# =============================================================================
# Voice tab — place a call + see the full per-call trace
# =============================================================================
def _render_voice_tab() -> None:
    st.subheader("📞 Voice agent")
    st.caption(
        "Enter a phone number and place a call. The voice agent talks to the "
        "caller using THIS planner as its brain. Below you can see exactly what "
        "was said and which booking APIs were called."
    )

    try:
        import sys as _sys
        from pathlib import Path as _Path

        _root = str(_Path(__file__).resolve().parent.parent)
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        import voice_service
    except Exception as e:  # noqa: BLE001
        st.error(f"voice_service unavailable: {e}")
        return

    provider = getattr(voice_service, "VOICE_CALL_PROVIDER", "livekit")
    if provider == "livekit":
        st.caption(
            "🟢 Provider: **LiveKit** (Sarvam voice, real-time heartbeats). "
            "The LiveKit worker (voice_livekit.py) must be running."
        )
    else:
        st.caption("🔵 Provider: **Vapi**")
        if not voice_service.VAPI_API_KEY:
            st.warning(
                "VAPI not configured. Set VAPI_API_KEY / VAPI_ASSISTANT_ID / "
                "VAPI_PHONE_NUMBER_ID in .env, or switch VOICE_CALL_PROVIDER=livekit."
            )

    col1, col2 = st.columns([3, 1])
    with col1:
        number = st.text_input(
            "Phone number", value="+91", key="voice_number",
            help="E.g. +918881310786 or a bare 10-digit Indian number.",
        )
    with col2:
        st.write("")
        st.write("")
        get_call = st.button("📞 Get a call", type="primary", use_container_width=True)

    # The voice SERVICE (uvicorn, the URL Vapi hits) runs as a separate process,
    # so the live trace lives there. Fetch it over HTTP, not from our own import.
    import os as _os

    svc_url = _os.getenv("VOICE_SERVICE_URL", "http://127.0.0.1:8100")

    if get_call:
        res = voice_service.place_call_auto(number)
        if res.get("error"):
            st.error(f"Call failed: {res['error']}")
        else:
            st.success(
                f"Calling {res.get('number')} now (status: {res.get('status')}). "
                "Pick up — your phone should ring within a few seconds."
            )

    st.divider()
    st.markdown("##### 🔎 Call trace (live)")
    if provider == "livekit":
        st.caption("Reading trace from the LiveKit worker (shared file).")
    else:
        st.caption(f"Reading trace from the voice service at {svc_url}")

    if "voice_traces" not in st.session_state:
        st.session_state.voice_traces = {}

    # Manual refresh only — no auto st.rerun() loop (that reloads the whole page
    # every couple seconds and makes the UI flicker/blur). Click to pull latest.
    if st.button("🔄 Refresh trace"):
        if provider == "livekit":
            # LiveKit worker is a separate process — it mirrors turns to a file.
            st.session_state.voice_traces = voice_service.read_trace_file()
        else:
            try:
                import urllib.request as _u
                with _u.urlopen(f"{svc_url}/trace", timeout=4) as r:
                    st.session_state.voice_traces = json.loads(r.read().decode())
            except Exception as e:  # noqa: BLE001
                st.warning(
                    f"Couldn't reach the voice service /trace at {svc_url} ({e}). "
                    "Is it running?"
                )

    traces: dict[str, Any] = st.session_state.voice_traces
    if not traces:
        st.info("No calls yet. Place a call above, talk to the agent, then click Refresh.")
        return

    def _truncate(obj, max_chars: int = 50000) -> str:
        s = json.dumps(obj, indent=2, default=str) if not isinstance(obj, str) else obj
        return s if len(s) <= max_chars else s[:max_chars] + f"\n... [{len(s)-max_chars} chars truncated]"

    # Most-recent session first. Only auto-expand the latest one.
    sessions = list(reversed(list(traces.items())))
    for idx, (session_id, turns) in enumerate(sessions):
        with st.expander(f"Call `{session_id}` — {len(turns)} turns", expanded=(idx == 0)):
            # Show only last 5 turns to avoid freezing on long calls
            visible_turns = list(enumerate(turns, 1))[-5:]
            if len(turns) > 5:
                st.caption(f"Showing last 5 of {len(turns)} turns.")
            for i, turn in reversed(visible_turns):
                total_s = turn.get('latency_s', '?')
                t_filler = turn.get('t_filler_s')
                t_results = turn.get('t_results_s')
                timing_str = f"total {total_s}s"
                if t_filler is not None:
                    timing_str += f"  ·  filler @{t_filler}s"
                if t_results is not None:
                    timing_str += f"  ·  results @{t_results}s"
                st.markdown(f"**Turn {i}**  ·  _{timing_str}_")
                st.markdown(f"🧑 **Caller:** {turn.get('user', '')}")

                # Filler / backchannel line
                filler_text = turn.get('filler')
                if filler_text:
                    st.markdown(f"💬 **Filler sent** `@{t_filler}s`: _{filler_text}_")

                # Heartbeats
                heartbeats = turn.get('heartbeats') or []
                for hb in heartbeats:
                    st.markdown(f"💓 **Heartbeat** `@{hb.get('t_s')}s`: _{hb.get('text')}_")

                if t_results is not None:
                    st.markdown(f"✅ **Results spoken** `@{t_results}s`")

                st.markdown(f"🤖 **Agent:** {turn.get('agent', '')}")
                tools = turn.get("tools") or []
                if tools:
                    for t in tools:
                        with st.expander(f"🛠️ `{t.get('tool')}`", expanded=False):
                            st.code(_truncate(t.get("input", {})), language="json")
                            st.caption("Output:")
                            st.code(_truncate(t.get("output", "")), language="json")
                calls = turn.get("api_calls") or []
                if calls:
                    for c in calls:
                        ep = c.get("url", "").split("gujjutours.com")[-1] or c.get("url", "")
                        hdr = f"{c.get('method')} {ep} → {c.get('status_code')} ({c.get('duration_ms')} ms)"
                        with st.expander(f"🌐 {hdr}", expanded=False):
                            st.code(_truncate(c.get("request_body")), language="json")
                            st.caption("Response:")
                            st.code(_truncate(c.get("response_body")), language="json")
                st.divider()


# =============================================================================
# Leads tab — upload social-comment JSON, AI-classify into hot/warm/cold leads
# =============================================================================
_TAG_STYLE = {
    "hot": ("🔥", "#ff4b4b"),
    "warm": ("🟠", "#ff9d00"),
    "cold": ("🧊", "#3b82f6"),
}


def _render_leads_tab() -> None:
    st.subheader("🎯 Lead Inspector")
    st.caption(
        "Upload a JSON of social-media comments (from a post). The AI reads each "
        "comment and flags the commenter as a hot / warm / cold travel lead with a "
        "score and reason. (Later this connects live to the Meta Graph API.)"
    )

    try:
        import sys as _sys
        from pathlib import Path as _Path

        _root = str(_Path(__file__).resolve().parent.parent)
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from lead_scoring import classify_leads, normalize_comments
    except Exception as e:  # noqa: BLE001
        st.error(f"lead_scoring unavailable: {e}")
        return

    uploaded = st.file_uploader(
        "Upload comments JSON", type=["json"], key="leads_upload",
        help="Array of comments with username, name, comment (extra fields are kept).",
    )
    use_llm = st.checkbox("Classify with AI (Claude)", value=True, key="leads_use_llm")

    if uploaded is not None and st.button("🔎 Analyze leads", type="primary"):
        try:
            raw = json.load(uploaded)
        except Exception as e:  # noqa: BLE001
            st.error(f"Couldn't read JSON: {e}")
            return
        comments = normalize_comments(raw)
        if not comments:
            st.warning("No comments found in that file. Expected a list of {username, name, comment}.")
            return
        with st.spinner(f"Analyzing {len(comments)} comments…"):
            st.session_state.lead_rows = classify_leads(comments, use_llm=use_llm)

    rows = st.session_state.get("lead_rows")
    if not rows:
        st.info("Upload a comments JSON and click **Analyze leads** to see the lead table.")
        return

    # Summary counts by tag.
    counts = {t: sum(1 for r in rows if r["tag"] == t) for t in ("hot", "warm", "cold")}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total leads", len(rows))
    c2.metric("🔥 Hot", counts["hot"])
    c3.metric("🟠 Warm", counts["warm"])
    c4.metric("🧊 Cold", counts["cold"])

    # Filter + table.
    tag_filter = st.multiselect(
        "Filter by tag", ["hot", "warm", "cold"], default=["hot", "warm", "cold"], key="leads_filter"
    )
    shown = [r for r in rows if r["tag"] in tag_filter]
    table = [
        {
            "Lead": f"{_TAG_STYLE.get(r['tag'], ('', ''))[0]} {r['tag'].upper()}",
            "Score": r["score"],
            "Name": r.get("name", ""),
            "Username": r.get("username", ""),
            "Comment": r.get("comment", ""),
            "Why": r.get("reason", ""),
        }
        for r in shown
    ]
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d"
            ),
            "Comment": st.column_config.TextColumn("Comment", width="large"),
        },
    )


# =============================================================================
# Chat
# =============================================================================
def _coerce_output(output: Any) -> Any:
    """Best-effort: turn a tool output into a JSON-friendly value for display.

    Tool results arrive as dicts or as JSON strings. Parse strings to dicts when
    possible so the debug panel can pretty-print them; otherwise keep the raw
    value (string, list, etc.) so nothing is hidden from the inspector.
    """
    if isinstance(output, (dict, list)):
        return output
    if isinstance(output, str):
        try:
            return json.loads(output)
        except (ValueError, TypeError):
            return output
    return output


def _summarize_tc(tc: dict[str, Any]) -> str:
    output = _coerce_output(tc.get("output"))
    if isinstance(output, dict):
        if output.get("error"):
            return f"error: {output.get('error_type', 'unknown')}"
        opts = output.get("options")
        if isinstance(opts, list):
            return f"{len(opts)} options"
        # Surface ROE / single-value tool results compactly.
        if "rate" in output and "source" in output:
            return f"{output.get('source')}: 1 {output.get('base_currency')} = ₹{output.get('rate')}"
    return "ok"


def _process_message(user_message: str) -> None:
    user_ts = _now_stamp()
    st.session_state.chat_history.append(
        {"role": "user", "content": user_message, "ts": user_ts}
    )
    st.session_state.turn_number += 1
    turn = st.session_state.turn_number

    with st.chat_message("user"):
        _render_timestamp(user_ts)
        st.markdown(user_message)

    # Mark the HTTP cursor BEFORE the turn so we can collect exactly the supplier
    # requests that fire during it and attribute them to this turn's tool calls.
    http_cursor = latest_http_seq()
    result = StreamResult()

    with st.chat_message("assistant"):
        # Stamp the timestamp when the AI actually STARTS RESPONDING (first token),
        # not at turn-start — otherwise it shows the time before the 2-10s of tool
        # work, which reads as "wrong". A placeholder is filled on the first token.
        ts_slot = st.empty()
        assistant_ts = None
        # Show a live "thinking / searching" status during the silent gap while
        # the agent reasons + calls booking APIs (before any text streams). As
        # tools fire, reflect which one in the status so the wait feels alive.
        status = st.status("✨ Planning your trip…", expanded=False)

        def _streamed():
            nonlocal assistant_ts
            seen_tools: set[str] = set()
            _labels = {
                "search_flights": "✈️ Searching flights…",
                "search_hotels": "🏨 Finding hotels…",
                "search_tours": "🎟️ Looking up tours & activities…",
                "search_airport_transfer_dubai": "🚐 Checking transfers…",
                "get_hotel_description": "🏊 Checking hotel amenities…",
                "get_visa_info": "📄 Checking visa details…",
                "search_restaurants": "🍽️ Finding restaurants…",
                "build_trip_schedule_tool": "🗓️ Laying out your schedule…",
            }
            gen = stream_and_log(
                st.session_state.agent,
                surface="streamlit",
                thread_id=st.session_state.thread_id,
                user_message=user_message,
                turn_number=turn,
                result=result,
            )
            for token in gen:
                # update status from tools observed so far this turn
                for ev in result.tool_event_log:
                    name = ev.get("tool_name", "")
                    if ev.get("event") == "call" and name and name not in seen_tools:
                        seen_tools.add(name)
                        status.update(label=_labels.get(name, f"🔧 {name}…"))
                # Stamp the moment the first visible token arrives.
                if assistant_ts is None and token:
                    assistant_ts = _now_stamp()
                    with ts_slot:
                        _render_timestamp(assistant_ts)
                yield token

        try:
            assistant_text = st.write_stream(_streamed)
        except Exception as _e:  # noqa: BLE001
            # The LLM provider can rate-limit (429) or DROP THE STREAM mid-generation
            # (common on flaky OpenRouter upstreams, esp. gemma/qwen free tiers — the
            # tool call succeeded and text was streaming, then the connection died).
            # Log the REAL error so we can tell a provider drop from a code bug.
            import logging as _lg
            _lg.getLogger("streamlit.turn").warning(
                "stream failed: %s: %s (partial_len=%d)",
                type(_e).__name__, str(_e)[:200], len(result.text or "")
            )
            emsg = str(_e)
            # If text already streamed before the drop, KEEP it — a partial real
            # answer beats wiping it out with a generic error.
            partial = (result.text or "").strip()
            if partial and len(partial.split()) >= 8:
                assistant_text = partial
                status.update(label="Stream cut short — kept partial reply", state="error")
            elif "429" in emsg or "rate-limit" in emsg.lower() or "RateLimit" in emsg:
                assistant_text = (
                    "I'm getting rate-limited by the model provider right now — "
                    "please send that again in a few seconds."
                )
                status.update(label="Rate-limited — retry", state="error")
                st.warning(assistant_text)
            else:
                assistant_text = (
                    "Sorry, the model connection dropped mid-reply. Please try that again."
                )
                status.update(label="Provider stream error — retry", state="error")
                st.warning(assistant_text)
        else:
            status.update(label="Done", state="complete")
        if not isinstance(assistant_text, str):
            assistant_text = result.text or ""
        # Fallback: nothing streamed (tool-only turn / error) → stamp now so the
        # message still carries a timestamp.
        if assistant_ts is None:
            assistant_ts = _now_stamp()
            with ts_slot:
                _render_timestamp(assistant_ts)

        # Real HTTP requests made during this turn (method + full URL + status).
        http_for_turn = http_requests_since(http_cursor)
        for rec in http_for_turn:
            st.session_state.http_calls.append({"turn": turn, **rec})

        tool_calls = extract_tool_calls(result.response)
        _record_debug_calls(turn, tool_calls, http_for_turn)

        # If the agent generated an itinerary PDF this turn, remember its id so
        # the sidebar can offer it for download.
        for tc in tool_calls:
            out = _coerce_output(tc.get("output"))
            if isinstance(out, dict) and out.get("itinerary_id"):
                st.session_state.itinerary_id = out["itinerary_id"]

        # Per-turn metadata for the "reused numbers" detector.
        st.session_state.turn_log.append(
            {
                "turn": turn,
                "user_msg": user_message,
                "n_tool_calls": len(tool_calls),
                "n_api": len(http_for_turn),
                "quoted_price": _mentions_price(assistant_text),
            }
        )

        # Always remember this turn's search results so a later "show me the
        # images" can re-display them without a fresh search.
        search = extract_search_options(result.response)
        if search.get("options"):
            st.session_state.last_search = {
                "kind": search["kind"],
                "options": search["options"],
            }

        # Decide whether to render cards, and of which kind:
        #  1) explicit display_options_tool call (production path), else
        #  2) keyword/regex heuristic (fallback).
        cards_payload: dict[str, Any] = {}
        signal_kind = _display_signal(tool_calls)
        render = bool(signal_kind) or _should_render_cards(user_message, assistant_text)
        if render:
            if search.get("options") and (not signal_kind or signal_kind == search["kind"]):
                kind, options = search["kind"], search["options"]
            else:
                # Reuse the last rendered set (e.g. "show the images" after a
                # prior tour search) — prefer the kind the agent asked to show.
                last = st.session_state.get("last_search") or {}
                kind, options = last.get("kind"), last.get("options") or []
                if signal_kind and kind and signal_kind != kind:
                    options = []  # asked for a kind we have no results for
            if options and kind:
                cards_payload = {"kind": kind, "options": options}
                for opt in options:
                    _render_option(kind, opt)

        # Calendar: if the agent built a schedule this turn, draw the time-grid.
        schedule_days = _schedule_signal(tool_calls)
        if schedule_days:
            _render_calendar(schedule_days)

        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": assistant_text,
                "cards": cards_payload,
                "schedule": schedule_days or None,
                "ts": assistant_ts,
            }
        )


def _record_debug_calls(
    turn: int, tool_calls: list[dict[str, Any]], http_for_turn: list[dict[str, Any]]
) -> None:
    """Store one rich debug record per tool call, attaching the real HTTP request(s).

    Tool calls and HTTP requests fire in the same order within a turn, so we
    match API-backed tools to the turn's HTTP records positionally. Local
    (compute-only) tools get no HTTP record — which is itself useful signal.
    """
    http_iter = iter(http_for_turn)
    for tc in tool_calls:
        name = tc.get("tool_name", "?")
        http_rec = next(http_iter, None) if name in _API_BACKED_TOOLS else None
        st.session_state.debug_calls.append(
            {
                "turn": turn,
                "tool_name": name,
                "input": tc.get("input") or {},
                "output": _coerce_output(tc.get("output")),
                "summary": _summarize_tc(tc),
                "http": http_rec,
            }
        )


# =============================================================================
# Main
# =============================================================================
_init_session()
_render_sidebar()

st.title("🏖️ Dubai Trip Planner")
# Show the ACTUAL active model (the live switcher's choice), not the env default —
# otherwise the header shows a stale model name that disagrees with the sidebar.
_active_model = st.session_state.get("model_override")
_provider_line = f"OpenRouter — {_active_model}" if _active_model else describe_current_provider()
st.caption(f"Powered by {_provider_line} · streaming on")

chat_tab, voice_tab, leads_tab, debug_tab = st.tabs(["💬 Chat", "📞 Voice", "🎯 Leads", "🔧 Debug"])

with chat_tab:
    # Empty-state hint so the chat doesn't look broken before the first message.
    if not st.session_state.chat_history:
        st.info(
            "👋 Tell me about your Dubai trip — origin city, dates/nights, who's "
            "travelling, and your budget. Every API and tool call shows live in the "
            "**🔧 Debug** tab so you can see exactly what data each answer is built on.",
            icon="🧭",
        )

    # Replay chat history — only render rich cards for the last 6 messages to
    # avoid rerendering all hotel/flight cards on every interaction (causes freeze).
    history = st.session_state.chat_history
    RICH_WINDOW = 6
    rich_start = max(0, len(history) - RICH_WINDOW)
    for i, entry in enumerate(history):
        with st.chat_message(entry.get("role", "assistant")):
            _render_timestamp(entry.get("ts"))
            st.markdown(entry.get("content", ""))
            if i < rich_start:
                continue  # skip heavy card rendering for old messages
            cards = entry.get("cards") or {}
            kind = cards.get("kind")
            options = cards.get("options") or []
            if kind and options:
                for opt in options:
                    _render_option(kind, opt)
            if entry.get("schedule"):
                _render_calendar(entry["schedule"])

    # "Generate itinerary PDF" button: build from confirmed trip details using
    # the generate_itinerary_pdf_tool (real, tool-sourced numbers). The name
    # must match the registered tool exactly — it previously said
    # "generate_itinerary_pdf", which does not exist, so the model had to guess
    # and the PDF often took two or three asks.
    if st.session_state.get("pdf_request_pending"):
        st.session_state.pdf_request_pending = False
        _process_message(
            "Please generate the itinerary PDF now using the trip details we've "
            "confirmed (origin, dates, party, the flights/hotel/tours/visa we "
            "discussed, the total, and the payment schedule). Call the "
            "generate_itinerary_pdf_tool with the real numbers — do not invent "
            "any, and do not ask me for details we already covered."
        )
        st.rerun()

with voice_tab:
    _render_voice_tab()

with leads_tab:
    _render_leads_tab()

with debug_tab:
    st.caption(describe_current_provider())
    _render_debug_inspector()

# Chat input — must be at top level (Streamlit requires st.chat_input outside
# tabs/columns). It drives the Chat tab.
user_input = st.chat_input("Ask me anything about your Dubai trip…")
if user_input:
    _process_message(user_input)
    st.rerun()
