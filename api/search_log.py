"""What each customer searched, derived from a reply's tool calls.

Pure functions only (no database, no agent imports), so the rules are unit
tested on their own. The SQL backfill in migrations/009_search_log.sql
applies the same rules to replies saved before the table existed; keep the
two in step when a rule changes.
"""

from __future__ import annotations

import json
from typing import Any

# Argument names the tools use, in the order they are tried.
_ORIGIN_KEYS = ("origin_city", "origin")
# Not hotel_name: a transfer names the hotel, and "Address Sky View" is not
# a destination (it showed up next to Dubai in the top destinations).
_DESTINATION_KEYS = ("destination_city", "destination_country", "destination", "city")
_DATE_KEYS = (
    "check_in", "travel_date", "departure_date", "arrival_date", "search_date",
    "start_date", "date",
)
# A plan's arguments can carry a whole list of tours; the admin view needs
# what was asked, not a copy of the payload.
_ARGS_MAX_CHARS = 8000


def _as_dict(value: Any) -> dict[str, Any] | None:
    """A tool input/output as a dict: ToolMessage content is JSON text."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) <= 6:
        return int(value.strip())
    return None


def _first_text(args: dict[str, Any], keys: tuple[str, ...], limit: int) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:limit]
    return None


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def party_of(args: dict[str, Any]) -> str | None:
    """"2 adults, 1 child, 1 room" from whatever party fields the tool took."""
    rooms_value = args.get("rooms")
    room_list = [r for r in rooms_value if isinstance(r, dict)] if isinstance(rooms_value, list) else []

    adults = _int(args.get("adults"))
    if adults is None and room_list:
        adults = sum(_int(r.get("adults")) or 0 for r in room_list) or None
    children = _int(args.get("children"))
    if children is None and isinstance(args.get("child_ages"), list):
        children = len(args["child_ages"])
    infants = _int(args.get("infants"))
    rooms = len(rooms_value) if isinstance(rooms_value, list) else _int(rooms_value)

    parts = [
        _plural(adults, "adult", "adults") if adults and adults > 0 else "",
        _plural(children, "child", "children") if children and children > 0 else "",
        _plural(infants, "infant", "infants") if infants and infants > 0 else "",
        _plural(rooms, "room", "rooms") if rooms and rooms > 0 else "",
    ]
    text = ", ".join(p for p in parts if p)
    return text or None


def result_count_of(output: dict[str, Any] | None) -> int | None:
    if not output:
        return None
    if isinstance(output.get("options"), list):
        return len(output["options"])
    total = _int(output.get("total_results"))
    if total is not None:
        return total
    for key in ("results", "items"):
        if isinstance(output.get(key), list):
            return len(output[key])
    return None


def error_of(output: dict[str, Any] | None, raw: Any) -> str | None:
    """The failure a tool reported, or None when it worked."""
    if output is not None:
        flag = output.get("error")
        if flag in (None, False, "", 0):
            return None
        message = output.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()[:300]
        if isinstance(flag, str):
            return flag.strip()[:300]
        return "failed"
    # A tool that raised comes back as plain text ("Error: ...").
    if isinstance(raw, str) and raw.strip().lower().startswith("error"):
        return raw.strip()[:300]
    return None


def _stored_args(args: dict[str, Any]) -> dict[str, Any]:
    try:
        text = json.dumps(args, default=str)
    except (TypeError, ValueError):
        return {"_unserialisable": True}
    if len(text) > _ARGS_MAX_CHARS:
        return {"_truncated": True}
    return json.loads(text)


def search_rows(tool_calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """One search_log row (without ids) per tool call, in call order.

    `call_index` is the call's position in the list, which with the message
    id makes a row unique, so writing the same reply twice adds nothing.
    """
    rows: list[dict[str, Any]] = []
    for index, call in enumerate(tool_calls or []):
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool_name") or call.get("name") or "").strip() or "unknown"
        args = _as_dict(call.get("input")) or {}
        raw_output = call.get("output")
        output = _as_dict(raw_output)
        rows.append({
            "call_index": index,
            "tool": tool[:80],
            "args": _stored_args(args),
            "origin": _first_text(args, _ORIGIN_KEYS, 120),
            "destination": _first_text(args, _DESTINATION_KEYS, 120),
            "start_date": _first_text(args, _DATE_KEYS, 40),
            "party": party_of(args),
            "result_count": result_count_of(output),
            "error": error_of(output, raw_output),
            "latency_ms": None,
        })
    return rows
