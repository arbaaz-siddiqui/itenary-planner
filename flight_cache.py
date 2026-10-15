"""flight_cache — session-scoped in-memory cache for flight search results.

All provider results for one search land here, keyed by everything that
changes the answer: route, dates, passengers (adults, children, infants),
cabin and the stop limit. Subsequent "show more" / "other airline" requests
read the cache — zero API calls.

Cabin and stop limit are part of the key because a business search asked
straight after an economy one used to be answered from the economy entry, so
the customer saw economy fares sold as business.

Entries expire after SESSION_TTL_SECONDS. Supplier fares move within the hour
(one FZ 438 fare went 84,296 -> 92,634 -> 97,506 in about an hour), so an entry
lives long enough for follow-up questions, not across a planning session. The
cache is process-scoped, so separate processes (voice vs chat) have independent
caches — correct behaviour since sessions don't cross surfaces.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

SESSION_TTL_SECONDS = 600  # 10 min


@dataclass
class _CacheEntry:
    options: list[dict[str, Any]]
    all_options: list[dict[str, Any]]   # full set across all providers
    providers_done: list[str]           # which airline codes responded
    providers_pending: list[str]        # still in-flight (background)
    search_params: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > SESSION_TTL_SECONDS

    def add_provider_results(self, airline_code: str, new_options: list[dict[str, Any]]) -> None:
        self.providers_done.append(airline_code)
        if airline_code in self.providers_pending:
            self.providers_pending.remove(airline_code)
        seen_keys = {o.get("fare_source_code") for o in self.all_options}
        for o in new_options:
            if o.get("fare_source_code") not in seen_keys:
                self.all_options.append(o)
                seen_keys.add(o.get("fare_source_code"))
        # re-sort by price
        self.all_options.sort(key=lambda o: o.get("price_inr", float("inf")))


_CACHE: dict[tuple, _CacheEntry] = {}


def _make_key(
    origin_iata: str,
    dest_iata: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    children: int,
    *,
    infants: int = 0,
    cabin: str = "Y",
    max_stops: int = 2,
) -> tuple:
    return (
        origin_iata.upper(),
        dest_iata.upper(),
        departure_date,
        return_date or "",
        adults,
        children,
        infants,
        (cabin or "Y").upper(),
        max_stops,
    )


def get_entry(
    origin_iata: str,
    dest_iata: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    children: int,
    *,
    infants: int = 0,
    cabin: str = "Y",
    max_stops: int = 2,
) -> _CacheEntry | None:
    key = _make_key(origin_iata, dest_iata, departure_date, return_date, adults, children,
                    infants=infants, cabin=cabin, max_stops=max_stops)
    entry = _CACHE.get(key)
    if entry is None:
        return None
    if entry.is_expired():
        del _CACHE[key]
        return None
    return entry


def set_entry(
    origin_iata: str,
    dest_iata: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    children: int,
    options: list[dict[str, Any]],
    providers_done: list[str],
    providers_pending: list[str],
    search_params: dict[str, Any],
    *,
    infants: int = 0,
    cabin: str = "Y",
    max_stops: int = 2,
) -> _CacheEntry:
    key = _make_key(origin_iata, dest_iata, departure_date, return_date, adults, children,
                    infants=infants, cabin=cabin, max_stops=max_stops)
    entry = _CacheEntry(
        options=options,
        all_options=list(options),
        providers_done=list(providers_done),
        providers_pending=list(providers_pending),
        search_params=search_params,
    )
    _CACHE[key] = entry
    return entry


def update_entry(
    origin_iata: str,
    dest_iata: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    children: int,
    new_options: list[dict[str, Any]],
    providers_done: list[str],
    providers_pending: list[str],
    airline_code: str = "",
    *,
    infants: int = 0,
    cabin: str = "Y",
    max_stops: int = 2,
) -> None:
    """Replace the entry's options with the current sorted snapshot.

    Called after each provider responds, including providers that answer after
    the search has already returned. `new_options` is the full sorted list so far.
    """
    key = _make_key(origin_iata, dest_iata, departure_date, return_date, adults, children,
                    infants=infants, cabin=cabin, max_stops=max_stops)
    entry = _CACHE.get(key)
    if entry is None or entry.is_expired():
        return
    entry.all_options = list(new_options)
    entry.options = list(new_options)
    entry.providers_done = list(providers_done)
    entry.providers_pending = list(providers_pending)


def fare_ref_for(fare_source_code: str) -> str:
    """Short, stable handle for a fare the model can quote back.

    The full fare_source_code is ~450 tokens, so the model gets this instead.
    A digest rather than a prefix: supplier codes are base64 of
    "<n>$$0$$<provider>$$<conversationId>...", so prefixes can coincide.
    """
    code = str(fare_source_code or "")
    return hashlib.sha1(code.encode("utf-8")).hexdigest()[:12] if code else ""


def find_option(fare_ref: str) -> dict[str, Any] | None:
    """The cached option a `fare_ref` (or a full fare_source_code) points at.

    Newest entries are searched first. None when nothing live matches.
    """
    ref = (fare_ref or "").strip()
    if not ref:
        return None
    for entry in reversed(list(_CACHE.values())):
        if entry.is_expired():
            continue
        for o in entry.all_options:
            code = str(o.get("fare_source_code") or "")
            if code and (ref == code or ref == fare_ref_for(code)):
                return o
    return None


def clear_expired() -> int:
    expired = [k for k, v in _CACHE.items() if v.is_expired()]
    for k in expired:
        del _CACHE[k]
    return len(expired)
