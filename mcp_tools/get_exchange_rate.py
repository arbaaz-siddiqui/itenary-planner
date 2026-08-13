"""get_exchange_rate — agent + MCP tool.

Rate of exchange (ROE) for the customer-facing currency. Wraps the supplier's
GET /api/Currency/ROE/{code} endpoint and reports how many INR one unit of the
supplier currency (AED by default) is worth.

Source of truth, in order:
  1. Live ROE from the supplier API (when reachable).
  2. The manual FX rate configured in CurrencySettings (fallback).

This tool exists so the agent answers "what's the ROE" with a REAL number and
its source — never an invented rate. If neither source yields a value, it says
so honestly rather than guessing.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_currency_roe
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_currency_roe_response
from settings import get_currency_settings

logger = logging.getLogger(__name__)


def _impl(base_currency: str = "", target_currency: str = "INR") -> dict[str, Any]:
    """Get the rate of exchange to the customer's currency (INR).

    Args:
        base_currency: Supplier currency to convert FROM (e.g. "AED"). Defaults
            to the configured ROE base currency (AED) when blank.
        target_currency: Customer currency to convert TO. Defaults to "INR".

    Returns a dict with the resolved `rate` (INR per 1 base unit), the `source`
    ("live_api" or "manual_fallback"), and the currencies — or an honest error
    when no rate is available. Never returns a fabricated rate.
    """
    settings = get_currency_settings()
    base = (base_currency or settings.roe_base_currency or "AED").strip().upper()
    target = (target_currency or "INR").strip().upper()
    manual_map = settings.as_rate_map()

    # 1. Try the live ROE endpoint. The supplier quotes ROE for the target
    #    currency (INR) as a single buying/selling rate, where sellingROE is the
    #    INR-per-base-unit figure the customer is charged at.
    live_rate: float | None = None
    live_error: str | None = None
    parsed: dict[str, Any] = {}
    try:
        raw = call_currency_roe(target_currency=target)
        parsed = parse_currency_roe_response(raw)
        live_rate = parsed.get("rate")
        if live_rate is None:
            logger.info("ROE response carried no usable rate (got: %s) — using fallback", parsed)
    except TripPlannerError as e:
        live_error = e.message
        logger.warning("get_exchange_rate live call failed, using manual fallback: %s", e)
    except Exception as e:  # never let FX lookup crash the agent
        live_error = str(e)
        logger.exception("get_exchange_rate unexpected error, using manual fallback")

    if live_rate is not None and live_rate > 0:
        return {
            "base_currency": base,
            "target_currency": target,
            "rate": round(live_rate, 4),
            "source": "live_api",
            "buying_roe": parsed.get("buying_roe"),
            "selling_roe": parsed.get("selling_roe"),
            "note": f"Live selling ROE from supplier: 1 {base} = {round(live_rate, 4)} {target}.",
        }

    # 2. Fall back to the manual configured rate (target must be INR for this map).
    if target == "INR":
        manual_rate = manual_map.get(base)
        if manual_rate is not None and manual_rate > 0:
            return {
                "base_currency": base,
                "target_currency": target,
                "rate": round(manual_rate, 4),
                "source": "manual_fallback",
                "note": (
                    f"Live ROE unavailable; using the configured rate "
                    f"1 {base} = {round(manual_rate, 4)} {target}. "
                    "This is a manually maintained rate, not a live market quote."
                ),
                "live_error": live_error,
            }

    return {
        "error": True,
        "message": (
            f"No rate of exchange available for {base}->{target}. The live ROE "
            "lookup failed and there is no configured fallback rate for this pair. "
            "Do not quote a rate you cannot verify."
        ),
        "error_type": "RateUnavailable",
        "live_error": live_error,
    }


from mcp_tools.result_cache import cache_impl
get_exchange_rate_tool = tool(cache_impl("get_exchange_rate")(_impl))
get_exchange_rate_tool.name = "get_exchange_rate"
mcp.tool(name="get_exchange_rate")(_impl)
