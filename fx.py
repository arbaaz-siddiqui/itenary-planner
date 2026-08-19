"""fx — live foreign-exchange rate map.

Supplier search/rate responses are priced in AED (or USD); the response-level
`Currency` says which. To quote the customer in INR we must multiply by the
CURRENT rate of exchange, not a stale constant. This module is the single
chokepoint every parser uses: it fetches the live ROE from the supplier
(`/api/Currency/ROE/INR`) and folds it into the rate map, falling back to the
manually configured rate when the live call is unavailable.

Why here and not in `parsers.py`: keeping the ROE fetch/parse in one place means
`parsers.py` imports `fx` (one direction, no cycle) and every component —
flights, hotels, tours, transfers, restaurants, visa, packages — converts at the
same live rate.

The live rate is cached briefly (ROE moves slowly; a multi-tool turn shouldn't
hit the network repeatedly). Pass a clock via `_now` only in tests.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from settings import get_currency_settings

logger = logging.getLogger(__name__)


def _live_disabled() -> bool:
    """When set (e.g. in tests), skip the network ROE call and use static rates.

    Keeps the test suite hermetic and deterministic — conversions use the
    configured AED rate, not whatever the live market is on the day tests run.
    """
    return os.environ.get("FX_DISABLE_LIVE_ROE", "").strip().lower() in {"1", "true", "yes"}

# Cache the live ROE for this many seconds. ROE is a daily-ish rate; this just
# stops a burst of parses in one turn from each making a ~900ms network call.
ROE_CACHE_TTL_SECS = 600.0

_LOCK = threading.Lock()
_cache: dict[str, tuple[float, float]] = {}  # currency -> (rate_inr, fetched_at)
# Per-currency fetch locks so concurrent callers (e.g. parallel flight providers
# all parsing at once) COALESCE into a single network ROE call instead of each
# firing their own. Without this the /api/Currency/ROE/INR call fires N times.
_fetch_locks: dict[str, threading.Lock] = {}
_fetch_locks_guard = threading.Lock()


def _fetch_lock_for(code: str) -> threading.Lock:
    with _fetch_locks_guard:
        lk = _fetch_locks.get(code)
        if lk is None:
            lk = _fetch_locks[code] = threading.Lock()
        return lk


def _selling_roe_from(raw: Any) -> float | None:
    """Pull the INR-per-1-base-unit selling rate from a ROE response.

    Live supplier shape: {"result": {"sellingROE": 26.36, "buyingROE": 0.0388, ...}}.
    Falls back to 1/buyingROE, then a flat rate/roe field. Returns None if none
    are usable (caller then uses the configured fallback).
    """
    payload = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(payload, dict):
        payload = raw if isinstance(raw, dict) else {}

    def _f(*keys: str) -> float | None:
        for k in keys:
            v = payload.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return None

    selling = _f("sellingROE", "SellingROE", "sellingRoe", "sellRate")
    if selling:
        return selling
    buying = _f("buyingROE", "BuyingROE", "buyingRoe", "buyRate")
    if buying:
        return 1.0 / buying
    return _f("rate", "Rate", "roe", "ROE", "exchangeRate", "value")


def live_rate_to_inr(currency: str, *, _now: float | None = None) -> tuple[float, str]:
    """Return (rate_inr_per_unit, source) for `currency` → INR.

    source is "live_api" or "manual_fallback". INR is always (1.0, "manual_fallback").
    Cached for ROE_CACHE_TTL_SECS. Never raises — the manual rate is the floor.
    """
    code = (currency or "INR").strip().upper()
    settings = get_currency_settings()
    manual = settings.as_rate_map().get(code)
    if code == "INR":
        return 1.0, "manual_fallback"

    now = _now if _now is not None else time.monotonic()
    with _LOCK:
        cached = _cache.get(code)
        if cached and (now - cached[1]) < ROE_CACHE_TTL_SECS:
            return cached[0], "live_api"

    # Only the configured ROE base currency (AED) has a supplier ROE endpoint we
    # trust; others keep their configured rate.
    live: float | None = None
    if not _live_disabled() and code == (settings.roe_base_currency or "AED").strip().upper():
        # Serialize concurrent fetches for this currency. The FIRST caller does the
        # network call; the rest block here, then find the fresh value in the cache
        # on the re-check below — so ROE is fetched ONCE per TTL, not once per caller.
        with _fetch_lock_for(code):
            recheck_now = time.monotonic() if _now is None else _now
            with _LOCK:
                cached = _cache.get(code)
                if cached and (recheck_now - cached[1]) < ROE_CACHE_TTL_SECS:
                    return cached[0], "live_api"
            try:
                from booking_api import call_currency_roe

                raw = call_currency_roe(target_currency="INR")
                live = _selling_roe_from(raw)
            except Exception as e:  # never let FX lookup break a parse
                logger.warning("live ROE fetch failed for %s, using manual rate: %s", code, e)
            if live and live > 0:
                with _LOCK:
                    _cache[code] = (live, recheck_now)
                return live, "live_api"

    if manual and manual > 0:
        return manual, "manual_fallback"
    # Last resort: 1:1 (should never happen for supported currencies).
    return 1.0, "manual_fallback"


def live_rate_map(*, _now: float | None = None) -> dict[str, float]:
    """Rate map (currency -> INR) with AED replaced by the live ROE when available.

    Drop-in replacement for `get_currency_settings().as_rate_map()` in parsers.
    USD/EUR/GBP/SGD keep their configured rates (no supplier ROE for them yet);
    AED uses the live selling ROE, falling back to the configured rate.
    """
    rates = dict(get_currency_settings().as_rate_map())
    base = (get_currency_settings().roe_base_currency or "AED").strip().upper()
    rate, _source = live_rate_to_inr(base, _now=_now)
    rates[base] = rate
    return rates


def supplier_pricing_roe(*, _now: float | None = None) -> float | None:
    """INR-per-AED rate the SUPPLIER prices at — i.e. `1 / buyingROE`.

    Not the same number as `live_rate_map()["AED"]`, which uses `sellingROE`.
    `/api/Currency/ROE/INR` returns both:

        {"buyingROE": 0.0387204523, "sellingROE": 26.3452500728}

    Verified against the visa endpoint's own `priceWithoutROE` figures: the
    supplier converts at `1/buyingROE` (25.826), NOT `sellingROE` (26.345).
    Using the selling rate overquotes a UAE visa by ₹282–₹537. Anywhere we
    must reproduce the supplier's OWN INR price, use this.

    Returns None when the live call fails — callers fall back to the
    supplier-provided INR field rather than inventing a rate.
    """
    try:
        from booking_api import call_currency_roe

        raw = call_currency_roe(target_currency="INR")
    except Exception as e:  # never let an FX lookup break a parse
        logger.warning("supplier pricing ROE fetch failed: %s", e)
        return None

    payload = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(payload, dict):
        payload = raw if isinstance(raw, dict) else {}
    for key in ("buyingROE", "BuyingROE", "buyingRoe", "buyRate"):
        v = payload.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return 1.0 / float(v)
    return None


def clear_fx_cache() -> None:
    """Drop the cached live rate (tests / forced refresh)."""
    with _LOCK:
        _cache.clear()
