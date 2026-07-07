"""flight_cache — session-scoped in-memory cache for flight search results.

When a multi-provider search fires, all provider results land here keyed by
(origin_iata, dest_iata, departure_date, return_date, adults, children).
The search tool returns the first batch immediately and stores the rest here.
Subsequent "show more" / "other airline" requests hit the cache — zero API calls.

Cache entries expire after SESSION_TTL_SECONDS (30 minutes) to avoid stale
fares. The cache is process-scoped, so separate processes (voice vs chat) have
independent caches — correct behaviour since sessions don't cross surfaces.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

SESSION_TTL_SECONDS = 1800  # 30 min


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
) -> tuple:
    return (
        origin_iata.upper(),
        dest_iata.upper(),
        departure_date,
        return_date or "",
        adults,
        children,
    )


def get_entry(
    origin_iata: str,
    dest_iata: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    children: int,
) -> _CacheEntry | None:
    key = _make_key(origin_iata, dest_iata, departure_date, return_date, adults, children)
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
) -> _CacheEntry:
    key = _make_key(origin_iata, dest_iata, departure_date, return_date, adults, children)
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
) -> None:
    """Bulk-update the cache entry with the current sorted snapshot.

    Called incrementally by the background fan-out thread after each provider
    responds. `new_options` is the full deduplicated+sorted list so far.
    """
    key = _make_key(origin_iata, dest_iata, departure_date, return_date, adults, children)
    entry = _CACHE.get(key)
    if entry is None or entry.is_expired():
        return
    entry.all_options = list(new_options)
    entry.options = list(new_options)
    entry.providers_done = list(providers_done)
    entry.providers_pending = list(providers_pending)


def clear_expired() -> int:
    expired = [k for k, v in _CACHE.items() if v.is_expired()]
    for k in expired:
        del _CACHE[k]
    return len(expired)
