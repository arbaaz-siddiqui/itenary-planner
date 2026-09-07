"""get_tour_options — the bookable variants of one tour.

A tour is not one product. "Desert Safari Tours in Dubai" has 12 variants
(Overnight/Evening x Shared/Private vehicle x add-ons), and Burj Khalifa has 12
(At the Top Silver, Fast Track, Level 148...), each with its own price, transfer
tiers, pax limits and timeslots. search_tours returns the tour; this returns
what you can actually book.
"""

from __future__ import annotations

import concurrent.futures as _cf
import logging
from typing import Any

from langchain_core.tools import tool

from core import TripPlannerError
from fx import convert_supplier_price

from .result_cache import cache_impl

logger = logging.getLogger(__name__)
mcp: Any = None

_FETCH_DEADLINE_S = 12.0
_MAX_WORKERS = 12


def _tier_rows(rate_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-transfer-type prices for one variant, Rs 0 kept as 'Included'."""
    out: list[dict[str, Any]] = []
    tiers = rate_row.get("initialTransferRates") or []
    has_paid = any(float(t.get("startingFromRate") or 0) > 0 for t in tiers)
    for t in tiers:
        raw = t.get("startingFromRate")
        if raw is None:
            continue
        # All-zero tiers mean rates are not loaded for this date, not "free".
        if not has_paid:
            return []
        if float(raw) == 0:
            out.append({
                "transfer_type": t.get("transferTypeName") or "",
                "price_inr": 0,
                "price_display": "Included (₹0)",
            })
            continue
        inr, _cur = convert_supplier_price(raw, fare_currency=t.get("currencyCode") or "AED")
        out.append({
            "transfer_type": t.get("transferTypeName") or "",
            "price_inr": inr,
            "price_display": f"₹{inr:,.0f}",
        })
    return out


def _pax_limits(option: dict[str, Any]) -> dict[str, Any]:
    """Min/max pax and rate basis, per transfer type, from validateTourOption."""
    limits: dict[str, dict[str, Any]] = {}
    for v in option.get("validateTourOption") or []:
        name = v.get("transferTypeName") or ""
        if not name:
            continue
        row = limits.setdefault(name, {
            "min_pax": v.get("minPax"),
            "max_pax": v.get("maxPax"),
            "rate_basis": v.get("rateTypeName") or "",
        })
        # PERSON vs VEHICLE changes what the price means — keep whichever is set.
        if not row["rate_basis"] and v.get("rateTypeName"):
            row["rate_basis"] = v["rateTypeName"]
    return limits


def _impl(
    tour_id: int,
    travel_date: str,
    adults: int = 2,
    max_results: int = 15,
) -> dict[str, Any]:
    """Variants of one tour: price, transfer tiers, pax limits, timeslots.

    Args:
        tour_id: from search_tours.
        travel_date: ISO yyyy-mm-dd.
        adults: party size — variant prices are pax-dependent.
        max_results: cap on variants returned.
    """
    from booking_api.endpoints import call_tour_option_rate, call_tour_options

    try:
        raw = call_tour_options(tour_id=int(tour_id), travel_date=travel_date)
    except TripPlannerError as e:
        # A bare error made the model improvise: it apologised for a "technical
        # glitch" and asked which hotel the customer was staying at (irrelevant
        # to tour variants) while priced tours sat on screen. Say what to do.
        return {
            "error": True,
            "message": str(e),
            "error_type": "TourOptionsFailed",
            "agent_instructions": (
                "The variant lookup failed — this says NOTHING about the tour's "
                "availability or price. Retry get_tour_options once with the "
                "same arguments. If it fails again, present the tour from the "
                "search_tours row you already have (its price and transfer "
                "prices are valid) and say you could not load the ticket "
                "variants right now, offering to re-check. Do NOT apologise for "
                "a technical glitch, do NOT ask which hotel or area they are "
                "staying in (that is irrelevant to tour variants), and do NOT "
                "ask for information the customer has already given."
            ),
        }

    listed = ((raw or {}).get("result") or {}).get("tourOptionlist") or []
    if not listed:
        return {
            "tour_id": int(tour_id),
            "options": [],
            "total_results": 0,
            "message": "The supplier lists no bookable variants for this tour on that date.",
        }

    listed = listed[: max(1, int(max_results))]

    def _price(option: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {
            "option_id": option.get("optionId"),
            "name": str(option.get("optionName") or "").strip(),
            "supplier": str(option.get("supplierName") or ""),
            "has_timeslots": bool(option.get("isTimeslot")),
            "pax_limits": _pax_limits(option),
            # Add-ons are extras bought ON TOP of a main variant, not a tour
            # you book alone. The supplier flags them only in the name.
            "is_addon": "add-on" in str(option.get("optionName") or "").lower(),
            # Who CANNOT book this variant (drinks packages exclude Child and
            # Infant) and which transfer tiers are barred for it.
            "not_available_for": [
                str(r.get("name") or "") for r in option.get("restrictedRateType") or []
                if r.get("name")
            ],
            "transfer_not_available": [
                str(r.get("transferTypeName") or r.get("name") or "")
                for r in option.get("tourRestrictedTransferType") or []
            ],
        }
        # The rate call is keyed on transferId. A variant only answers on the
        # tiers it supports: add-ons ("2 Drinks Package") are Without-Transfers
        # only, so a hardcoded transferId=1 got result:null and the variant read
        # "On request" while the website priced it. Try the variant's own tiers
        # first, then the standard three.
        own = [v.get("transferTypeId") for v in option.get("validateTourOption") or []]
        rows: list[dict[str, Any]] = []
        for tid in [*dict.fromkeys(t for t in own if t), 1, 3, 2]:
            try:
                rate_raw = call_tour_option_rate(
                    tour_id=int(tour_id),
                    option_id=option.get("optionId"),
                    supplier_id=int(option.get("supplierId") or 0),
                    travel_date=travel_date,
                    adults=max(1, int(adults or 1)),
                    transfer_id=int(tid),
                )
                rows = (rate_raw or {}).get("result") or []
            except Exception as e:  # noqa: BLE001 — a variant without a rate still lists
                logger.debug("option rate failed for %s: %s", option.get("optionId"), e)
                rows = []
            if rows and rows[0].get("rate"):
                break
        if rows:
            rate = rows[0].get("rate")
            if rate:
                inr, _cur = convert_supplier_price(
                    rate, fare_currency=rows[0].get("currencyCode") or "AED"
                )
                row["price_inr"] = inr
                row["price_display"] = f"₹{inr:,.0f}"
            # Every variant, add-ons included, carries its own transfer tiers —
            # verified against the website to the paisa (add-on 113117: Sharing
            # 62 AED = Rs 1,619.06, Private 400 AED = Rs 10,445.52). Show them
            # all; suppressing them on add-ons hid real, bookable prices.
            tiers = _tier_rows(rows[0])
            if tiers:
                row["transfer_prices"] = tiers
                row["transfer_price_display"] = " · ".join(
                    f"{t['transfer_type']}: {t['price_display']}" for t in tiers
                )
        if "price_display" not in row:
            row["price_display"] = "On request"
        return row

    ex = _cf.ThreadPoolExecutor(max_workers=max(1, min(_MAX_WORKERS, len(listed))))
    try:
        futures = [ex.submit(_price, o) for o in listed]
        done, _pending = _cf.wait(futures, timeout=_FETCH_DEADLINE_S)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    options = [f.result() for f in futures if f in done and not f.exception()]
    options.sort(key=lambda r: (r.get("price_inr") is None, r.get("price_inr") or 0))

    priced = sum(1 for o in options if o.get("price_inr"))
    # Some tours price every variant identically (Burj: 14 x Rs 1,783). Say so,
    # so the reply can lead with the one price instead of repeating it 14 times
    # — a wall of identical rows is what the model started trimming.
    distinct = {o.get("price_inr") for o in options if o.get("price_inr")}
    same_price_note = ""
    if len(distinct) == 1 and priced == len(options) and len(options) > 3:
        only = next(iter(distinct))
        same_price_note = (
            f" All {len(options)} variants are the same price (₹{only:,.0f}), so "
            "state that once and list the variant names — do not repeat the "
            "price on every row."
        )
    return {
        "tour_id": int(tour_id),
        "options": options,
        "total_results": len(options),
        "table_markdown": _options_table(options),
        "agent_instructions": (
            f"PASTE `table_markdown` VERBATIM — all {len(options)} rows, none "
            "dropped. These are the bookable variants of ONE tour (Evening vs "
            "Overnight, Shared vs Private vehicle, ticket tiers), which is what "
            "the customer sees on our website. Quote `price_display` exactly; "
            "'On request' means the supplier returned no rate — never invent "
            "one. `transfer_prices` is the only source for transfer costs. "
            "`pax_limits` gives min/max pax and whether the rate is per PERSON "
            "or per VEHICLE — say which, because a per-vehicle price is for the "
            "whole group. For a variant with `has_timeslots`, call "
            "get_tour_timeslots with its option_id for real start times. "
            "Rows marked `is_addon` are EXTRAS bought on top of a main variant "
            "(drinks package, private majlis), never booked alone — list them "
            "under the main options and ASK whether they want any add-ons, with "
            "each add-on's own price. The Notes column carries the booking "
            "rules: an add-on needs a main option chosen first, "
            "`not_available_for` lists pax types that CANNOT take that variant "
            "(e.g. a drinks package excludes Child and Infant), and "
            "`transfer_not_available` lists transfer tiers barred for it. "
            "Relay those constraints — do not offer a variant to a party it "
            "excludes."
            + same_price_note
            + ("" if priced else " NOTE: the supplier returned no prices for any "
               "variant on this date — list them by name and say pricing is "
               "confirmed on request.")
        ),
    }


def _notes_cell(o: dict[str, Any]) -> str:
    """Booking constraints: add-on status, who cannot book, barred transfers."""
    bits: list[str] = []
    if o.get("is_addon"):
        bits.append("add-on — needs a main option")
    if o.get("not_available_for"):
        bits.append("not for " + "/".join(o["not_available_for"]))
    if o.get("transfer_not_available"):
        bits.append("no " + "/".join(o["transfer_not_available"]))
    return "; ".join(bits) or "-"


def _options_table(options: list[dict[str, Any]]) -> str:
    """One markdown row per variant — built here so no row can be dropped."""
    head = (
        "| Option | Price | Transfer | Pax | Timeslots | Notes |"
        + chr(10)
        + "|---|---|---|---|---|---|"
    )
    rows = []
    # Main variants first, add-ons after — an add-on is bought on top of one.
    for o in sorted(options, key=lambda r: bool(r.get("is_addon"))):
        limits = o.get("pax_limits") or {}
        pax_bits = []
        for name, lim in limits.items():
            # Only PERSON/VEHICLE mean anything to a customer; the supplier's
            # "OTHER"/"Adult"/"Child" values would read as nonsense ("per other").
            raw = (lim.get("rate_basis") or "").strip().upper()
            basis = f" per {raw.lower()}" if raw in {"PERSON", "VEHICLE"} else ""
            pax_bits.append(f"{name}: {lim.get('min_pax')}-{lim.get('max_pax')}{basis}")
        cells = [
            ("(add-on) " if o.get("is_addon") else "") + str(o.get("name") or ""),
            str(o.get("price_display") or ""),
            str(o.get("transfer_price_display") or "-"),
            "; ".join(pax_bits) or "-",
            "Yes" if o.get("has_timeslots") else "-",
            _notes_cell(o),
        ]
        rows.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return chr(10).join([head, *rows])


get_tour_options_tool = tool(cache_impl("get_tour_options")(_impl))
get_tour_options_tool.name = "get_tour_options"
