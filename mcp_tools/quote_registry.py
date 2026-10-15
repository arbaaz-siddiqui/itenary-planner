"""Prices the customer has actually been quoted, per conversation.

A package total is built from figures the model passes back to
plan_itinerary_tool. The model sometimes passes a figure from the wrong
result: asked for a package with no airport transfer in it, it passed the
Desert Safari's Rs 5,350.66 as `transfer_total_inr`, and the package gained
an airport transfer nobody had searched for. Tools record what they quoted
here, so the planner can accept a figure only when it really was quoted in
this conversation.

Keyed by `rules.current_thread()`, never global, so one customer's quotes are
never visible in another customer's chat.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

_LOCK = threading.Lock()
_MAX_THREADS = 500
# thread -> kind -> amounts quoted
_QUOTES: OrderedDict[str, dict[str, list[float]]] = OrderedDict()


def remember(kind: str, amounts: list[float]) -> None:
    from rules import current_thread

    key = current_thread()
    clean = [round(float(a), 2) for a in amounts if isinstance(a, (int, float)) and a > 0]
    if not clean:
        return
    with _LOCK:
        per = _QUOTES.setdefault(key, {})
        per.setdefault(kind, []).extend(clean)
        _QUOTES.move_to_end(key)
        while len(_QUOTES) > _MAX_THREADS:
            _QUOTES.popitem(last=False)


def was_quoted(kind: str, amount: float, *, tolerance: float = 1.0) -> bool:
    """True when `amount` matches a `kind` price quoted in this conversation."""
    from rules import current_thread

    with _LOCK:
        seen = list((_QUOTES.get(current_thread()) or {}).get(kind) or [])
    return any(abs(a - float(amount)) <= tolerance for a in seen)
