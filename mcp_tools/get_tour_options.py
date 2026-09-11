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


def _tier_rows_by_difference(
    *,
    tour_id: int,
    option: dict[str, Any],
    travel_date: str,
    adults: int,
    base_total_inr: float,
    own: list[Any],
) -> list[dict[str, Any]]:
    """Transfer prices derived from what each tier actually costs.

    `base_total_inr` is the transferId=3 total (the ticket alone). A tier's
    price is its own total minus that, which is the only figure that matches
    the client website: for nine adults on Dune Bashing the site shows a
    sharing grand total of Rs 24,207.98 against ticket Rs 8,069 -- a transfer
    of Rs 16,139, where `initialTransferRates` claimed 0.
    """
    names = {1: "Sharing Transfer", 2: "Private Transfer"}

    # `tourRestrictedTransferType` is the supplier saying a tier is BLOCKED for
    # this variant -- the inverse of `validateTourOption`, which lists what is
    # allowed. Add-ons ("2 Drinks Package", "Private Majlis") restrict Sharing
    # AND Private, because they are bought on top of a main option and carry no
    # transfer of their own; main tours usually restrict Without Transfer, i.e.
    # a transfer is compulsory.
    #
    # Quoting a restricted tier offers a transfer the supplier will refuse to
    # book, which is what showed "Sharing Transfer: Rs 1,793" under an add-on.
    blocked = {
        str(r.get("transferTypeName") or "").strip().lower()
        for r in option.get("tourRestrictedTransferType") or []
    }
    out: list[dict[str, Any]] = []
    if "without transfer" not in blocked:
        out.append({
            "transfer_type": "Without Transfer",
            "price_inr": 0,
            "price_display": "Included (₹0)",
            "priced_for_pax": adults,
        })
    for tid in (1, 2):
        # Only tiers this variant supports, so we never quote a transfer the
        # supplier will refuse to book.
        if tid not in own or names[tid].lower() in blocked:
            continue
        from booking_api.endpoints import call_tour_option_rate

        try:
            raw = call_tour_option_rate(
                tour_id=tour_id,
                option_id=option.get("optionId"),
                supplier_id=int(option.get("supplierId") or 0),
                travel_date=travel_date,
                adults=adults,
                transfer_id=tid,
            )
            rows = (raw or {}).get("result") or []
        except Exception as e:  # noqa: BLE001 — one tier failing is not fatal
            logger.debug("tier rate failed for %s tid=%s: %s", option.get("optionId"), tid, e)
            continue
        if not rows or not rows[0].get("rate"):
            continue
        total, _cur = convert_supplier_price(
            rows[0].get("rate"), fare_currency=rows[0].get("currencyCode") or "AED"
        )
        if total is None:
            continue
        extra = round(float(total) - base_total_inr, 2)
        out.append({
            "transfer_type": names[tid],
            "price_inr": max(0.0, extra),
            # Already the total for `adults` people: the supplier applies its
            # own vehicle multiple, so this must never be multiplied again.
            "price_display": "Included (₹0)" if extra <= 0 else f"₹{extra:,.0f}",
            "priced_for_pax": adults,
        })
    return out


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tour_image_url(tour_id: int, travel_date: str) -> str:
    """The tour's own image, for variants to inherit.

    The supplier publishes NO image on a variant row (verified: tour 30647's
    five options carry no image/media key at all) -- variants are ticket types
    of ONE tour, so they share its picture. Without this every variant card
    rendered an empty placeholder.

    Cached at the STATIC tier: a tour's photo does not change with the date or
    the party size, so this costs at most one catalogue call per hour and is
    usually free because the customer just ran the search that populates it.
    """
    from booking_api.endpoints import call_tour_search
    from parsers import DEFAULT_IMAGE_BASE_URL, _resolve_image_url
    from reference_data_loader import resolve_city

    from .result_cache import _STATIC_TTL, cached_or_call

    def _produce() -> dict[str, Any]:
        city = resolve_city("Dubai") or {}
        if not city.get("city_id"):
            return {"images": {}}
        raw = call_tour_search(
            country_id=int(city["country_id"]),
            city_id=int(city["city_id"]),
            travel_date=travel_date,
        )
        # Read the image straight off the raw list rather than going through
        # parse_tour_response: that drops every tour with no rate, which would
        # need a second (rate) call for data we do not want here.
        listed = (raw.get("result") or {}).get("tourStaticlists") or []
        images: dict[str, str] = {}
        for t in listed:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("tourId") or t.get("tourID") or "")
            url = _resolve_image_url(t.get("imagePath"), DEFAULT_IMAGE_BASE_URL)
            if tid and url:
                images[tid] = url
        return {"images": images}

    try:
        got = cached_or_call(
            "tour_images", {"travel_date": travel_date}, _produce, ttl=_STATIC_TTL
        )
        return str((got.get("images") or {}).get(str(tour_id)) or "")
    except Exception as e:  # noqa: BLE001 — a missing picture must never fail the lookup
        logger.debug("tour image lookup failed for %s: %s", tour_id, e)
        return ""


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
    from rules import party_size_said

    # Prices here are TOTALS for `adults`, so a result fetched for a different
    # party size cannot be reused: asked about 8 people the model relayed a
    # private transfer of Rs 34,560, the 1-pax figure (4,320) times eight,
    # against a real Rs 4,468. Re-fetch for the size the customer stated
    # rather than returning a figure that will be multiplied.
    stated = party_size_said()
    if stated and stated != int(adults or 0):
        adults = stated

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
        # The supplier states the pricing basis in the option name, which is
        # the only place it appears: "... - Per Person (Add-on)" against
        # "Private Majlis - Per Group up to 6 Guests (Add-on)".
        option_name_l = str(option.get("optionName") or "").lower()
        row: dict[str, Any] = {
            "option_id": option.get("optionId"),
            "name": str(option.get("optionName") or "").strip(),
            "supplier": str(option.get("supplierName") or ""),
            # Both ids travel with the row so the UI can fetch this variant's
            # inclusions/exclusions on demand without a second lookup.
            "tour_id": int(tour_id),
            "supplier_id": _int_or_none(option.get("supplierId")),
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
        # transferId 3 = Without Transfer, i.e. the TICKET price on its own, and
        # it must be tried first. The variant's own tiers used to lead, so for
        # Dhow Marina (own = [1, 3, 3]) tid=1 won and returned 215.27 AED — the
        # ticket WITH sharing transfer bundled in. We then listed "Sharing
        # Transfer: Rs 741" beside it, so the transfer was counted twice and the
        # Lower Deck read Rs 5,630 where the website shows Rs 2,074 per adult.
        # The website prices the ticket alone and adds transfers separately;
        # quoting tid=3 keeps us aligned with it and with `transfer_prices`.
        own = [v.get("transferTypeId") for v in option.get("validateTourOption") or []]
        rows: list[dict[str, Any]] = []
        for tid in dict.fromkeys([3, *(t for t in own if t), 1, 2]):
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
                # `rate` is the total for the whole party, not per person —
                # 79.31 AED for 1 adult and 158.62 for 2 on the same variant.
                # It was shown under a column headed "Price", so a 2-adult
                # search read Rs 4,148 where the website says Rs 2,074 per
                # adult. Expose both, and label which is which.
                pax = max(1, int(adults or 1))
                row["price_total_inr"] = inr
                row["price_per_adult_inr"] = round(inr / pax, 2)
                row["pax_priced"] = pax
                row["price_inr"] = row["price_per_adult_inr"]
                # The supplier names the basis in the option name, and it is
                # NOT always per person: "Private Majlis - Per Group up to 6
                # Guests" is one majlis for the party. Note the supplier still
                # multiplies that by pax (726 / 1,452 / 4,356 AED at pax
                # 1/2/6), so we report what it will actually bill and say the
                # item is group-priced, rather than dividing it down to a
                # per-adult figure it will not honour.
                per_group = "per group" in option_name_l
                if per_group:
                    row["price_basis"] = "per_group"
                    row["price_display"] = (
                        f"₹{inr:,.0f} for {pax} guest(s) — priced per group"
                    )
                    row["price_note"] = (
                        "The supplier bills this group item once per guest in "
                        "the party. Confirm the final charge at booking."
                    )
                else:
                    row["price_basis"] = "per_adult"
                    row["price_display"] = (
                        f"₹{row['price_per_adult_inr']:,.0f} per adult"
                    )
                row["price_total_display"] = f"₹{inr:,.0f} total for {pax} adult(s)"
            # Every variant, add-ons included, carries its own transfer tiers —
            # verified against the website to the paisa (add-on 113117: Sharing
            # 62 AED = Rs 1,619.06, Private 400 AED = Rs 10,445.52). Show them
            # all; suppressing them on add-ons hid real, bookable prices.
            # The cost of a transfer is the DIFFERENCE between that tier's
            # total and the ticket-only total, NOT `initialTransferRates`.
            #
            # That field reports Sharing = 0 on every Dubai tour we checked,
            # while the real rates say sharing costs Rs 16,139 for nine people
            # on this variant -- so trusting it told the customer a paid
            # transfer was free. Same fix as search_tours; this path was missed.
            #
            # There is NO fallback to `initialTransferRates` here. Without a
            # ticket-only baseline a tier's price cannot be worked out, and
            # the old fallback answered "Included (Rs 0)" for a transfer the
            # customer would be charged for. Showing nothing is recoverable;
            # quoting zero for a paid transfer is not.
            base_total = row.get("price_total_inr")
            tiers = (
                _tier_rows_by_difference(
                    tour_id=int(tour_id),
                    option=option,
                    travel_date=travel_date,
                    adults=max(1, int(adults or 1)),
                    base_total_inr=float(base_total),
                    own=own,
                )
                if base_total
                else []
            )
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
        # The tour's picture is fetched ALONGSIDE the pricing fan-out, not
        # before it: it is usually a cache hit, and on a miss it must not add
        # its latency to the head of the critical path.
        img_future = ex.submit(_tour_image_url, int(tour_id), travel_date)
        done, _pending = _cf.wait(futures, timeout=_FETCH_DEADLINE_S)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    options = [f.result() for f in futures if f in done and not f.exception()]

    # Variants are ticket types of ONE tour and the supplier gives them no
    # image of their own, so they inherit the tour's. Without this every
    # variant card rendered an empty placeholder.
    try:
        image_url = img_future.result(timeout=0.1)
    except Exception:  # noqa: BLE001 — a card without a picture still works
        image_url = ""
    if image_url:
        for row in options:
            row["image_url"] = image_url
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
            "one. `price_display` is the TICKET price PER ADULT and excludes "
            "transfers, matching the website's own option cards; "
            "`price_total_display` is the same ticket for the whole party. "
            "For a group total ALWAYS quote `price_total_inr` — NEVER "
            "multiply the per-adult figure yourself. The per-adult price is "
            "ROUNDED for display (896.56 shows as Rs 897), so 897 x 9 gives "
            "Rs 8,073 where the supplier charges Rs 8,069 and the party "
            "total then misses the client website by Rs 4. Add "
            "`price_total_inr` to the transfer figure for the final total. "
            "Never add a transfer figure into the ticket price — the two are "
            "separate lines, and bundling them once showed a Rs 2,074 ticket "
            "as Rs 5,630. `transfer_prices` is the only source for transfer "
            "costs, and it is an EXTRA on top of the ticket. "
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
        "| Option | Price/adult | Transfer (extra) | Pax | Timeslots | Notes |"
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
