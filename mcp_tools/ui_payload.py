"""Full results for the screen, slim results for the model.

A search returns everything the supplier gave us: photos, menu photos, the
week's opening hours, long document descriptions. The cards need all of it.
The model needs almost none of it to answer, and every extra field costs
context, latency and money on every later turn, because tool results stay in
the conversation history.

So each search tool's result is split. The full result is kept here, keyed
by a short `ui_ref`; the model receives a slim copy carrying the same
`ui_ref`, with names, ids, prices and policies intact so it can still answer
any question about any card. The chat API builds the cards from the full copy
and stores them with the message, so they survive a reload even though this
store is in memory.
"""

from __future__ import annotations

import copy
import functools
import threading
import time
import uuid
from typing import Any

# Kept long enough for the turn that produced them to finish and be saved.
_TTL_SECS = 3600.0
_MAX_ENTRIES = 500

_STORE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()

# Fields only a card uses. Each is either a picture, a duplicate of a field the
# model keeps, or long text the model has a short form of.
_UI_ONLY = ("image_url", "image_urls", "dish_photos", "airline_logo", "airline_logos", "unique_key")
# Dropped only when the short form named alongside is present.
_UI_ONLY_IF = {
    "documents": "document_requirements",
    "opening_hours": "opening_hours_display",
    "meal_timings": "meal_timings_display",
}

UI_NOTE = (
    "The customer sees a card for every option, with its photos and full "
    "details, and can open or book any of them. When they refer to one by "
    "name, it is the option of that name here. The cards show prices PER "
    "PERSON, so your reply must give the TOTAL for the customer's party for "
    "each option you mention, quoted from the result (party_total_display, "
    "the totals in transfer_prices or prices, price_inr), never worked out "
    "yourself."
)


def register(full: dict[str, Any]) -> str:
    ref = uuid.uuid4().hex[:12]
    now = time.monotonic()
    with _LOCK:
        _STORE[ref] = (now, full)
        if len(_STORE) > _MAX_ENTRIES:
            for key in [k for k, (ts, _) in _STORE.items() if now - ts > _TTL_SECS]:
                _STORE.pop(key, None)
            while len(_STORE) > _MAX_ENTRIES:
                _STORE.pop(min(_STORE, key=lambda k: _STORE[k][0]), None)
    return ref


def fetch(ref: Any) -> dict[str, Any] | None:
    """The full result behind a `ui_ref`, or None once it has expired."""
    if not isinstance(ref, str):
        return None
    with _LOCK:
        hit = _STORE.get(ref)
    if not hit or time.monotonic() - hit[0] > _TTL_SECS:
        return None
    return hit[1]


def _slim_option(option: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in option.items() if k not in _UI_ONLY}
    for key, short_form in _UI_ONLY_IF.items():
        if key in out and option.get(short_form):
            out.pop(key)
    return out


def split(result: Any) -> Any:
    """Keep the full result for the screen, return the model's slim copy.

    Only results that carry a list of options are split; anything else (an
    error, a refusal asking for a date) passes through untouched.
    """
    if not isinstance(result, dict) or not isinstance(result.get("options"), list):
        return result
    if not result["options"]:
        return result
    ref = register(copy.deepcopy(result))
    slim = dict(result)
    slim["options"] = [_slim_option(o) if isinstance(o, dict) else o for o in result["options"]]
    slim["ui_ref"] = ref
    slim["ui_note"] = UI_NOTE
    return slim


def full_result(parsed: dict[str, Any]) -> dict[str, Any]:
    """The full result for a slim one the model saw, or the slim one if gone."""
    return fetch(parsed.get("ui_ref")) or parsed


def wrap_tool(tool: Any) -> Any:
    """Route a structured tool's result through `split`. Schema is untouched."""
    func = getattr(tool, "func", None)
    if func is None or getattr(func, "_ui_split", False):
        return tool

    @functools.wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return split(func(*args, **kwargs))

    wrapped._ui_split = True  # type: ignore[attr-defined]
    tool.func = wrapped
    return tool
