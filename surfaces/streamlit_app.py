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


def _init_session() -> None:
    if "agent" not in st.session_state:
        st.session_state.agent = build_react_agent(
            surface="streamlit", checkpoint_store=build_in_memory_checkpoint()
        )
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
    st.session_state.setdefault("turn_number", 0)


# =============================================================================
# Card detection
# =============================================================================
CARD_TRIGGER_RE = re.compile(
    r"^\s*here\s+are\s+the\s+top\s+\d+\s+"
    r"(flights?|hotels?|tours?|transfers?|restaurants?|visa\s+options?)\s*:",
    re.IGNORECASE,
)
USER_DISPLAY_KEYWORDS = ("show me", "list", "compare", "any options", "what are the options")


def _should_render_cards(user_text: str, agent_text: str) -> bool:
    if CARD_TRIGGER_RE.search(agent_text or ""):
        return True
    t = (user_text or "").lower()
    return any(kw in t for kw in USER_DISPLAY_KEYWORDS)


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


def _render_hotel(o: dict[str, Any]) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            star_str = "⭐" * int(o.get("stars", 0))
            st.markdown(f"**🏨 {o.get('hotel_name') or 'Hotel'}** {star_str}")
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
                st.image(o["image_url"], use_container_width=True)
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
    "search_transfers",
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


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("🌴 Trip Planner")
        _render_itinerary_section()
        st.divider()
        st.subheader("🔧 Debug")
        st.caption(describe_current_provider())
        _render_debug_inspector()


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
    st.session_state.chat_history.append({"role": "user", "content": user_message})
    st.session_state.turn_number += 1
    turn = st.session_state.turn_number

    with st.chat_message("user"):
        st.markdown(user_message)

    # Mark the HTTP cursor BEFORE the turn so we can collect exactly the supplier
    # requests that fire during it and attribute them to this turn's tool calls.
    http_cursor = latest_http_seq()
    result = StreamResult()

    with st.chat_message("assistant"):
        # st.write_stream consumes the token generator and live-renders the
        # assistant text as it arrives; it returns the full concatenated string.
        assistant_text = st.write_stream(
            stream_and_log(
                st.session_state.agent,
                surface="streamlit",
                thread_id=st.session_state.thread_id,
                user_message=user_message,
                turn_number=turn,
                result=result,
            )
        )
        if not isinstance(assistant_text, str):
            assistant_text = result.text or ""

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

        cards_payload: dict[str, Any] = {}
        if _should_render_cards(user_message, assistant_text):
            search = extract_search_options(result.response)
            if search.get("options"):
                cards_payload = {"kind": search["kind"], "options": search["options"]}
                for opt in search["options"]:
                    _render_option(search["kind"], opt)

        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": assistant_text,
                "cards": cards_payload,
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
st.caption(f"Powered by {describe_current_provider()} · streaming on")

# Empty-state hint so the chat doesn't look broken before the first message.
if not st.session_state.chat_history:
    st.info(
        "👋 Tell me about your Dubai trip — origin city, dates/nights, who's "
        "travelling, and your budget. Every API and tool call shows live in the "
        "**🔧 Debug** sidebar so you can see exactly what data each answer is built on.",
        icon="🧭",
    )

# Replay chat history
for entry in st.session_state.chat_history:
    with st.chat_message(entry.get("role", "assistant")):
        st.markdown(entry.get("content", ""))
        cards = entry.get("cards") or {}
        kind = cards.get("kind")
        options = cards.get("options") or []
        if kind and options:
            for opt in options:
                _render_option(kind, opt)

# "Generate itinerary PDF" button: ask the agent to build it from the confirmed
# trip details using the generate_itinerary_pdf tool (real, tool-sourced numbers).
if st.session_state.get("pdf_request_pending"):
    st.session_state.pdf_request_pending = False
    _process_message(
        "Please generate the itinerary PDF now using the trip details we've "
        "confirmed (origin, dates, party, the flights/hotel/tours/visa we "
        "discussed, the total, and the payment schedule). Call the "
        "generate_itinerary_pdf tool with the real numbers — do not invent any."
    )
    st.rerun()

# Chat input — the single entry point now that Quickstart is gone.
user_input = st.chat_input("Ask me anything about your Dubai trip…")
if user_input:
    _process_message(user_input)
    st.rerun()
