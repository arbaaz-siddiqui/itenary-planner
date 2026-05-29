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

import re
import uuid
from typing import Any

import streamlit as st

from agent import (
    build_in_memory_checkpoint,
    build_react_agent,
    configure_logging,
    extract_assistant_text,
    extract_search_options,
    extract_tool_calls,
    invoke_and_log,
)
from core import format_inr
from llm import describe_current_provider
from reference_data_loader import list_destinations, list_indian_origins

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
    st.session_state.setdefault("api_calls", [])
    st.session_state.setdefault("budget_total", 0.0)
    st.session_state.setdefault("budget_spent", 0.0)
    st.session_state.setdefault("trip_summary", {})
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
# Sidebar
# =============================================================================
def _render_sidebar() -> dict[str, Any]:
    with st.sidebar:
        st.header("🌴 Trip Planner")

        st.subheader("Quickstart")
        with st.form("quickstart_form", clear_on_submit=False):
            origin = st.selectbox("From", list_indian_origins(), index=0)
            destination = st.selectbox("To", list_destinations(), index=0)
            check_in = st.date_input("Check-in")
            check_out = st.date_input("Check-out")
            c1, c2 = st.columns(2)
            adults = c1.number_input("Adults", min_value=1, max_value=10, value=2)
            children = c2.number_input("Children", min_value=0, max_value=8, value=0)
            budget_inr = st.number_input(
                "Budget (INR)",
                min_value=0,
                value=100000,
                step=5000,
                help="Total budget per group (not per person)",
            )
            submitted = st.form_submit_button("Plan my trip")

        st.divider()
        st.subheader("Budget")
        bt = st.session_state.get("budget_total", 0)
        bs = st.session_state.get("budget_spent", 0)
        if bt > 0:
            pct = min(100, int(bs / bt * 100))
            st.progress(pct / 100, text=f"{pct}% used")
            st.caption(f"Spent: {format_inr(bs)} of {format_inr(bt)}")
            st.caption(f"Remaining: {format_inr(max(0, bt - bs))}")
        else:
            st.caption("No budget set yet")

        st.divider()
        st.subheader("Selected so far")
        summary = st.session_state.get("trip_summary", {})
        if not summary:
            st.caption("Nothing selected yet")
        else:
            for component, value in summary.items():
                st.write(f"**{component.capitalize()}:** {value}")

        with st.expander("🔧 API calls (debug)"):
            calls = st.session_state.get("api_calls", [])
            if not calls:
                st.caption("No API calls yet")
            else:
                for call in calls[-20:]:
                    st.caption(call)

    return {
        "submitted": submitted,
        "origin": origin,
        "destination": destination,
        "check_in": (check_in.isoformat() if hasattr(check_in, "isoformat") else str(check_in)),
        "check_out": (check_out.isoformat() if hasattr(check_out, "isoformat") else str(check_out)),
        "adults": int(adults),
        "children": int(children),
        "budget_inr": float(budget_inr),
    }


# =============================================================================
# Chat
# =============================================================================
def _summarize_tc(tc: dict[str, Any]) -> str:
    output = tc.get("output")
    if isinstance(output, dict):
        if output.get("error"):
            return f"error: {output.get('error_type', 'unknown')}"
        opts = output.get("options")
        if isinstance(opts, list):
            return f"{len(opts)} options"
    return "ok"


def _process_message(user_message: str) -> None:
    st.session_state.chat_history.append({"role": "user", "content": user_message})
    st.session_state.turn_number += 1

    with st.chat_message("user"):
        st.markdown(user_message)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            response = invoke_and_log(
                st.session_state.agent,
                surface="streamlit",
                thread_id=st.session_state.thread_id,
                user_message=user_message,
                turn_number=st.session_state.turn_number,
            )

        assistant_text = extract_assistant_text(response)
        tool_calls = extract_tool_calls(response)

        for tc in tool_calls:
            st.session_state.api_calls.append(f"{tc.get('tool_name', '?')} → {_summarize_tc(tc)}")

        st.markdown(assistant_text)

        cards_payload: dict[str, Any] = {}
        if _should_render_cards(user_message, assistant_text):
            search = extract_search_options(response)
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


# =============================================================================
# Main
# =============================================================================
_init_session()
quick = _render_sidebar()

st.title("🏖️ Dubai Trip Planner")
st.caption(f"Powered by {describe_current_provider()}")

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

# Quickstart form submission
if quick["submitted"] and quick["budget_inr"] > 0:
    st.session_state.budget_total = quick["budget_inr"]
    seed = (
        f"I want to plan a Dubai trip. "
        f"Departing from {quick['origin']} on {quick['check_in']}, "
        f"returning on {quick['check_out']}. "
        f"{quick['adults']} adults"
        + (f", {quick['children']} children" if quick["children"] else "")
        + f". My budget is ₹{quick['budget_inr']:,.0f}."
    )
    _process_message(seed)
    st.rerun()

# Chat input
user_input = st.chat_input("Ask me anything about your Dubai trip…")
if user_input:
    _process_message(user_input)
    st.rerun()
