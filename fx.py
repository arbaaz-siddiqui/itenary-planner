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
ROE_CACHE_TTL_SECS = 300.0  # 5 min: rates drift intraday, so re-fetch after this

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


def account_currency_code() -> str:
    """The agent's billing currency, from `creditlimitCurrencyCode` in the JWT.

    The supplier's conversion rule keys off this, so it must come from the token
    rather than be assumed. Ours is "INR". Falls back to INR if the claim is
    missing or the token cannot be decoded.
    """
    import base64
    import json as _json

    try:
        from settings import get_booking_api_settings

        tok = get_booking_api_settings().token or ""
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(part))
        code = str(claims.get("creditlimitCurrencyCode") or "").strip().upper()
        return code or "INR"
    except Exception as e:  # noqa: BLE001 — never let token parsing break a parse
        logger.warning("could not read creditlimitCurrencyCode from token: %s", e)
        return "INR"


# Cache the (buying, selling) pair per currency. WITHOUT this, converting a
# 270-tour list made 270 HTTP calls and parsing took 43 SECONDS. ROE is a
# daily-ish rate, so the existing ROE_CACHE_TTL_SECS window is plenty.
_ROE_PAIR_CACHE: dict[str, tuple[tuple[float | None, float | None], float]] = {}
_ROE_PAIR_LOCK = threading.Lock()


def _roe_pair(target_currency: str) -> tuple[float | None, float | None]:
    """(buyingROE, sellingROE) for a currency, or (None, None) if unavailable.

    Cached for ROE_CACHE_TTL_SECS — see the note above; this is a hot path.
    """
    code = (target_currency or "INR").strip().upper()
    # Hold the lock across the fetch so N concurrent callers make ONE call.
    # Releasing it first meant a 15-tour parallel search fetched ROE 15 times.
    with _ROE_PAIR_LOCK:
        now = time.monotonic()
        hit = _ROE_PAIR_CACHE.get(code)
        if hit and (now - hit[1]) < ROE_CACHE_TTL_SECS:
            return hit[0]
        pair = _fetch_roe_pair(code)
        # Only cache a usable answer, so a transient failure retries next call.
        if pair[0] or pair[1]:
            _ROE_PAIR_CACHE[code] = (pair, time.monotonic())
        return pair


def _fetch_roe_pair(target_currency: str) -> tuple[float | None, float | None]:
    """Uncached network fetch of the ROE pair."""
    try:
        from booking_api import call_currency_roe

        raw = call_currency_roe(target_currency=target_currency)
    except Exception as e:  # never let an FX lookup break a parse
        logger.warning("ROE fetch failed for %s: %s", target_currency, e)
        return None, None
    payload = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(payload, dict):
        payload = raw if isinstance(raw, dict) else {}

    def _pick(*keys: str) -> float | None:
        for k in keys:
            v = payload.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return None

    return (
        _pick("buyingROE", "BuyingROE", "buyingRoe", "buyRate"),
        _pick("sellingROE", "SellingROE", "sellingRoe", "sellRate"),
    )


def convert_supplier_price(
    price: float,
    *,
    fare_currency: str = "",
    display_currency: str | None = None,
) -> tuple[float | None, str]:
    """Convert a supplier fare to the currency we quote in.

    The supplier's own rule (confirmed by the client, 2026-08-21):

        Base/system currency is ALWAYS AED, so `fareInfo.price` is in AED even
        when the row is labelled `currency: "INR"`.

        if creditlimitCurrencyCode == fareInfo.currency:  price / buyingROE
        else:                                             price * sellingROE

    Worked examples from the client, both reproduced by this function:
        300.868335 AED / 0.0387979638 (INR buying)  = 7750.00 INR
        300.868335 AED * 0.2750170184 (USD selling) =   82.75 USD

    Note this INCLUDES the supplier's service fee, so it is the total the
    customer pays. It is deliberately ~fee larger than the endpoint's own
    `priceWithoutROE` field (which is the fare NET of the fee).

    Returns (amount, currency_code). Amount is None when no rate is available —
    callers must then fall back rather than invent a number.
    """
    try:
        amount = float(price)
    except (TypeError, ValueError):
        return None, ""
    if amount <= 0:
        return None, ""

    target = (display_currency or account_currency_code() or "INR").strip().upper()
    # IMPORTANT: the rule compares the account currency against
    # `fareInfo.currency` — the row's OWN label — not against the AED base. The
    # visa endpoint labels its rows "INR", so for an INR account the labels match
    # and we DIVIDE by buyingROE. Defaulting this to "AED" inverted the branch
    # and overquoted by ~2% (7921 instead of 7765).
    fare_cur = (fare_currency or target).strip().upper()

    buying, selling = _roe_pair(target)
    # INR is quoted in whole rupees — paisa on a visa fee reads like a bug.
    # Minor units matter for USD/EUR, so only INR-like currencies are rounded.
    places = 0 if target in {"INR", "JPY"} else 2

    # The branch is on whether the fare row is denominated in OUR currency.
    if fare_cur == target:
        if buying:
            return round(amount / buying, places), target
    elif selling:
        return round(amount * selling, places), target
    # One retry on the other rate rather than returning nothing.
    if buying:
        return round(amount / buying, places), target
    return None, target


def supplier_pricing_roe(*, _now: float | None = None) -> float | None:
    """Multiplier that turns an AED fare into the account currency (1/buyingROE).

    Kept for callers that want a plain rate. `convert_supplier_price()` is the
    preferred entry point — it implements the supplier's full currency rule.
    """
    buying, _ = _roe_pair(account_currency_code())
    return (1.0 / buying) if buying else None


def clear_fx_cache() -> None:
    """Drop the cached live rates (tests / forced refresh)."""
    with _LOCK:
        _cache.clear()
    with _ROE_PAIR_LOCK:
        _ROE_PAIR_CACHE.clear()
