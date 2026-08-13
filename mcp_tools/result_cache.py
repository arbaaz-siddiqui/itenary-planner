"""result_cache — a small, general cache for search-tool results.

Why: within one session a customer often re-asks for the same thing ("show the
hotels again", "what were those transfers"). Re-hitting the supplier API is slow
and pointless when the data hasn't changed. This caches each search tool's LAST
result per (tool, params) key so the tool can return it instantly.

Scope: process-wide, thread-safe, TTL'd. Flights have their OWN richer cache in
search_flights.py (fast-first + background backfill); this is the general one for
hotels/tours/transfers/etc.

Every cached return carries `"from_cache": True` and a `"cached_at"` so the agent
trace + logs can show the answer came from cache, not a fresh API call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# How long a cached result stays fresh. Supplier availability/pricing drifts, so
# keep this short enough to stay honest but long enough to help within a session.
CACHE_TTL_SECS = 600.0

# Per-tool TTL tiers. VOLATILE data (live availability + prices) expires fast so
# the agent re-checks the supplier often; STATIC content (descriptions, reviews,
# visa rules, entity lookups) can live much longer since it rarely changes.
_VOLATILE_TTL = 180.0    # 3 min — availability/price-bearing searches
_STATIC_TTL = 3600.0     # 1 hr — descriptions, reviews, visa, lookups
_TOOL_TTL = {
    # volatile (availability + prices) — short TTL, re-check often
    "search_hotels": _VOLATILE_TTL,
    "search_airport_transfer_dubai": _VOLATILE_TTL,
    "search_tours": _VOLATILE_TTL,
    "search_restaurants": _VOLATILE_TTL,
    "list_packages": _VOLATILE_TTL,
    "get_exchange_rate": _VOLATILE_TTL,
    # static content — long TTL
    "get_hotel_info": _STATIC_TTL,
    "get_hotel_description": _STATIC_TTL,
    "get_hotel_reviews": _STATIC_TTL,
    "get_tour_details": _STATIC_TTL,
    "get_tour_options": _STATIC_TTL,
    "get_tour_option_details": _STATIC_TTL,
    "get_transfer_details": _STATIC_TTL,
    "get_flight_details": _VOLATILE_TTL,
    "get_restaurant_details": _STATIC_TTL,
    "get_package_details": _STATIC_TTL,
    "get_visa_info": _STATIC_TTL,
    "list_visa_countries": _STATIC_TTL,
    "list_city_hotels": _STATIC_TTL,
    "lookup_entity": _STATIC_TTL,
    "lookup_hotel_city": _STATIC_TTL,
}

# BUMP THIS whenever a tool's OUTPUT SHAPE OR SEMANTICS CHANGE.
#
# Why this exists: the key used to be sha1(tool_name + kwargs) with no notion of
# code version. A fix to a tool therefore kept serving the PRE-FIX result for up
# to the full TTL — an hour for the static tier. That is not hypothetical: a
# customer was told "I couldn't find a Howard Johnson in Dubai. Most of the ones
# I see are in the US or China" from a 20-minute-old cached lookup_entity result
# created before the city-scoping fix shipped. The code was correct; the answer
# was stale. It also made the bug unreproducible in a fresh process, which cost
# a lot of debugging time.
#
# History:
#   1 — initial
#   2 — lookup_entity city scoping; hotel/flight payload slimming
CACHE_EPOCH = 2

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[Any, float]] = {}  # key -> (result, stored_at)

# Unbounded growth guard: entries were never evicted (stale ones were merely
# ignored), so full hotel/flight payloads accumulated for the process lifetime.
_MAX_ENTRIES = 512


def _key(tool: str, params: dict[str, Any]) -> str:
    blob = json.dumps(
        {"v": CACHE_EPOCH, "t": tool, "p": params}, sort_keys=True, default=str
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def cached_or_call(
    tool: str,
    params: dict[str, Any],
    producer: Callable[[], dict[str, Any]],
    *,
    ttl: float = CACHE_TTL_SECS,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return a cached result for (tool, params) if fresh, else call `producer`,
    store, and return it. On a HIT the returned dict is tagged from_cache=True and
    a "[CACHE] HIT ..." line is logged so the trace shows the answer was reused.

    The cache is a CONVENIENCE for genuine repeats — it must NEVER make the agent
    serve stale data when freshness matters. It is bypassed (fresh API call) when:
      - force_refresh=True  (agent passes it: "check again", "is it still available",
        or right before booking) — see cache_impl's force_refresh handling,
      - the params differ at all (different date/city/pax → different key → MISS),
      - the entry is older than `ttl` (per-tool; volatile availability = short TTL).
    So different requests and explicit re-checks ALWAYS hit the live tool.
    """
    k = _key(tool, params)
    now = time.monotonic()
    if force_refresh:
        logger.info("[CACHE] BYPASS %s — force_refresh, calling supplier", tool)
    else:
        with _LOCK:
            hit = _CACHE.get(k)
            if hit and (now - hit[1]) < ttl:
                result, stored = hit
                logger.info("[CACHE] HIT  %s (age %ds) — no API call", tool, int(now - stored))
                out = dict(result)
                out["from_cache"] = True
                out["cached_age_secs"] = int(now - stored)
                return out
        logger.info("[CACHE] MISS %s — calling supplier", tool)
    result = producer()

    # Cache any real, non-error dict that carries data. Search tools expose
    # total_results; detail/lookup tools don't but are just as reusable — so we
    # accept a non-empty dict (more than just bookkeeping keys) as cacheable too.
    cacheable = False
    if isinstance(result, dict) and not result.get("error"):
        if (result.get("total_results") or 0) > 0:
            cacheable = True
        else:
            # detail/lookup result: cache if it carries a real payload
            payload_keys = [k for k in result if k not in ("from_cache", "cached_age_secs", "error")]
            cacheable = len(payload_keys) > 0 and any(result.get(k) for k in payload_keys)
    if cacheable:
        with _LOCK:
            _CACHE[k] = (result, now)
            if len(_CACHE) > _MAX_ENTRIES:
                # Drop expired entries first; if still over, evict oldest-stored.
                for dead in [kk for kk, (_, ts) in _CACHE.items()
                             if now - ts > _STATIC_TTL]:
                    _CACHE.pop(dead, None)
                while len(_CACHE) > _MAX_ENTRIES:
                    oldest = min(_CACHE, key=lambda kk: _CACHE[kk][1])
                    _CACHE.pop(oldest, None)
    out = dict(result) if isinstance(result, dict) else result
    if isinstance(out, dict):
        out["from_cache"] = False
    return out


def clear_result_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def cache_impl(tool_name: str, *, ttl: float | None = None) -> Callable:
    """Decorator for a search tool's `_impl`: caches its result by call kwargs.

        _impl = cache_impl("search_hotels")(_impl)

    Uses functools.wraps so langchain's tool() still sees the ORIGINAL signature
    (and builds the correct args schema). On a cache hit the result carries
    from_cache=True and logs "[CACHE] HIT ...".

    TTL defaults to the per-tool tier (_TOOL_TTL): volatile availability/price
    tools get a short TTL, static content a long one.

    force_refresh: if the wrapped `_impl` declares a `force_refresh` parameter,
    the agent can pass it to BYPASS the cache and hit the live supplier (for
    "check again" / "is it still available" / pre-booking confirmation). We pop it
    before caching so it doesn't pollute the cache key, and pass it through to the
    tool only if the tool actually accepts it.

    NOTE: relies on callers passing args as KEYWORDS (langchain tool calls always
    do). Positional args are still folded into the key defensively."""
    import functools
    import inspect

    def deco(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        effective_ttl = ttl if ttl is not None else _TOOL_TTL.get(tool_name, CACHE_TTL_SECS)
        accepts_force = "force_refresh" in inspect.signature(fn).parameters

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            # force_refresh is a cache directive, not a search param — pull it out
            # so it never affects the cache key. Pass through only if the tool
            # itself declared the param.
            force = bool(kwargs.pop("force_refresh", False)) if not accepts_force else bool(kwargs.get("force_refresh", False))
            params = {"_args": list(args), **kwargs} if args else dict(kwargs)
            params.pop("force_refresh", None)  # never key on it
            return cached_or_call(
                tool_name, params, lambda: fn(*args, **kwargs),
                ttl=effective_ttl, force_refresh=force,
            )
        return wrapper
    return deco
