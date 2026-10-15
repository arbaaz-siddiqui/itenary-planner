"""Tour variants and their transfer prices, straight from the supplier."""

from __future__ import annotations

import concurrent.futures as cf
from collections.abc import Sequence
from typing import Any

from booking_api.endpoints import (
    call_tour_option_description,
    call_tour_option_rate,
    call_tour_options,
)
from core import format_inr
from fx import convert_supplier_price

# The supplier's transferTypeId. Names come from its own rows; these are only
# the fallback when a row carries none.
_TRANSFER_NAMES = {1: "Sharing Transfer", 2: "Private Transfer", 3: "Without Transfer"}


def party_label(adults: int, child_ages: Sequence[int] = ()) -> str:
    """"2 adults + 2 children (ages 5, 8)" -- who a total was priced for."""
    label = f"{adults} adult{'s' if adults != 1 else ''}"
    ages = list(child_ages or ())
    if ages:
        label += (
            f" + {len(ages)} child{'ren' if len(ages) != 1 else ''}"
            f" (age{'s' if len(ages) != 1 else ''} {', '.join(map(str, ages))})"
        )
    return label


def offered_transfers(option: dict[str, Any]) -> list[tuple[int, str]]:
    """Transfer types this option is actually sold with, in supplier order.

    `validateTourOption` lists every type the tour knows; the option's
    `tourRestrictedTransferType` names the ones it is NOT sold with (an add-on
    is ticket-only, a safari is never sold without pickup). gujjutours.com
    offers exactly the difference, so that is what we price.
    """
    restricted = {
        r.get("transferTypeId") for r in option.get("tourRestrictedTransferType") or []
    }
    seen: dict[int, str] = {}
    for v in option.get("validateTourOption") or []:
        tid = v.get("transferTypeId")
        if isinstance(tid, int) and tid not in restricted and tid not in seen:
            seen[tid] = str(v.get("transferTypeName") or _TRANSFER_NAMES.get(tid, ""))
    return list(seen.items())


def _vehicle_for(rate_row: dict[str, Any], pax: int) -> int:
    """The supplier's vehicle id for a party this size, or 0 when it has none."""
    for v in rate_row.get("vehicleRates") or []:
        if int(v.get("minPax") or 0) <= pax <= int(v.get("maxPax") or 0):
            return int(v.get("vehicleId") or 0)
    return 0


def sold_per_unit(option: dict[str, Any]) -> bool:
    """True when the option is priced per unit (one quad bike), not per person.

    `restrictedRateType` lists the rate types the option is NOT sold with. A
    quad bike restricts Adult and Child and validates only "Unit"; the website
    then asks "how many units" and prices one unit by default.
    """
    restricted = {
        str(r.get("name") or "").strip().lower()
        for r in option.get("restrictedRateType") or []
    }
    kinds = {
        str(v.get("rateTypeName") or "").strip().lower()
        for v in option.get("validateTourOption") or []
    }
    return "adult" in restricted and "unit" in kinds


def party_prices(
    tour_id: int,
    option: dict[str, Any],
    travel_date: str,
    adults: int,
    child_ages: Sequence[int] = (),
) -> list[dict[str, Any]]:
    """What the whole party pays for one option, per transfer type.

    One optionRate call per offered type, each made for the real party. The
    supplier's `rate` on that call is the grand total gujjutours.com charges --
    ticket, transfer and as many vehicles as the party needs -- so it is quoted
    as it is. Rebuilding it from per-head and per-vehicle parts is what made
    the agent add a private transfer on top of a sharing-inclusive ticket
    (Rs 16,689 quoted where the website charges Rs 13,290.34). Converted to the
    paisa because the customer compares it against the website line by line.

    A type the supplier will not price for this party (a 500 over its own
    ceiling) is left out rather than guessed. Cheapest first.
    """
    per_unit = sold_per_unit(option)
    # A per-unit option is quoted for ONE unit, as the website does: how many
    # bikes a party of four rides is the customer's choice, not ours.
    pax = 1 if per_unit else adults + len(child_ages or ())
    supplier_id = int(option.get("supplierId") or 0)

    def rate(tid: int, vehicle_id: int = 0) -> dict[str, Any]:
        return call_tour_option_rate(
            tour_id=tour_id,
            option_id=option["optionId"],
            supplier_id=supplier_id,
            travel_date=travel_date,
            adults=adults,
            child_ages=() if per_unit else child_ages,
            units=1 if per_unit else 0,
            transfer_id=tid,
            vehicle_id=vehicle_id,
        )["result"][0]

    def one(kind: tuple[int, str]) -> dict[str, Any] | None:
        tid, name = kind
        try:
            row = rate(tid)
            # A private transfer on a tour with named vehicles (Burj Khalifa's
            # Toyota Hiace) is priced only with that vehicle's id; without it
            # the supplier returns the ticket-only rate, which made Private
            # look free. The response names the vehicle for this party size.
            vehicle = _vehicle_for(row, pax) if tid == 2 else 0
            if vehicle:
                row = rate(tid, vehicle)
        except Exception:
            return None
        inr, _ = convert_supplier_price(
            row.get("rate"), fare_currency=row.get("currencyCode") or "AED", places=2
        )
        if not inr:
            return None
        return {
            "transfer_type": name,
            "transfer_type_id": tid,
            "total_inr": inr,
            "total_display": _paise(inr),
            "per_person_display": format_inr(round(inr / max(1, pax), 2)),
            "priced_for_pax": pax,
            "priced_per": "unit" if per_unit else "party",
            # Back-compat keys: earlier rows called the figure price_inr.
            "price_inr": inr,
            "price_display": format_inr(inr),
        }

    kinds = offered_transfers(option)
    if not kinds:
        return []
    with cf.ThreadPoolExecutor(max_workers=len(kinds)) as ex:
        priced = [p for p in ex.map(one, kinds) if p]
    return sorted(priced, key=lambda p: p["total_inr"])


def _paise(amount: float) -> str:
    """₹5,926.00, not ₹5,926: the website always shows paise on tour totals."""
    shown = format_inr(amount)
    return shown if "." in shown else f"{shown}.00"


def prices_display(prices: list[dict[str, Any]], party: str) -> str:
    """"Sharing Transfer: ₹5,350.66 · Private Transfer: ₹13,290.34 (total for 2 adults)"."""
    if not prices:
        return ""
    return (
        " · ".join(f"{p['transfer_type']}: {p['total_display']}" for p in prices)
        + f" (total for {party}, ticket included)"
    )


def option_policies(tour_id: int, option: dict[str, Any]) -> dict[str, str]:
    """The option's own cancellation and child policy, in the supplier's words.

    Read from the option description rather than a policy-id table: the same
    tour can mix "Free Cancellation Before 24 Hours" and "Non Refundable"
    options, and an id we have not mapped would otherwise read "TBC".
    """
    from parsers import parse_tour_option_description

    try:
        sections = parse_tour_option_description(
            call_tour_option_description(
                tour_id=tour_id,
                tour_option_id=option["optionId"],
                supplier_id=int(option.get("supplierId") or 0),
            )
        )
    except Exception:
        return {}

    def first(*titles: str) -> str:
        for want in titles:
            for sec in sections:
                if sec["title"].strip().lower() != want:
                    continue
                summary = sec["summary"].rstrip(".")
                # "Option Wise Cancellation Policy." is a heading echo, not terms.
                if summary and summary.lower() != want:
                    return summary
                if sec["items"]:
                    return sec["items"][0]
        return ""

    def details(title: str) -> list[str]:
        for sec in sections:
            if sec["title"].strip().lower() == title:
                return sec["items"]
        return []

    out = {
        "cancellation": first("option wise cancellation policy", "cancellation policy"),
        "cancellation_details": " ".join(
            details("option wise cancellation policy description")
            or details("option wise cancellation policy")
        ),
        "child_policy": " ".join(details("option wise child policy description")),
    }
    return {k: v for k, v in out.items() if v}


def tour_image_url(tour_id: int, travel_date: str) -> str:
    """The tour's own image, for its variants to share.

    Variant rows carry no image of their own, so without this every variant
    card renders an empty placeholder. Cached at the static tier: a photo does
    not change with the date or the party size.
    """
    from booking_api.endpoints import call_tour_search
    from parsers import DEFAULT_IMAGE_BASE_URL, _resolve_image_url
    from reference_data_loader import resolve_city

    from .result_cache import _STATIC_TTL, cached_or_call

    def _produce() -> dict[str, Any]:
        city = resolve_city("Dubai") or {}
        if not city.get("city_id"):
            return {"images": {}}
        listed = (
            call_tour_search(
                country_id=int(city["country_id"]),
                city_id=int(city["city_id"]),
                travel_date=travel_date,
            )
            .get("result", {})
            .get("tourStaticlists")
            or []
        )
        images = {}
        for t in listed:
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
    except Exception:
        return ""


def _booking_ceiling(option: dict[str, Any]) -> int:
    """Largest party the supplier prices in one booking for this option."""
    caps = [
        int(v.get("maxPax") or 0)
        for v in option.get("validateTourOption") or []
        if v.get("transferTypeId") == 2
    ]
    return max(caps, default=12) or 12


def _private_for_large_party(
    tour_id: int, option: dict[str, Any], travel_date: str, adults: int
) -> dict[str, Any] | None:
    """Private transfer for a party the supplier will not price in one booking.

    It returns HTTP 500 above its own ceiling (13+ on most tours), but 17
    people is a real booking -- it just needs more vehicles. The seat count is
    NOT maxPax: Desert Safari reports maxPax 12 while its price doubles at 7
    pax, so a car holds 6 and 12 is a two-car ceiling. Probing where the price
    actually steps gives the true capacity and the per-vehicle rate, and the
    quote is that rate times the number of cars needed.
    """
    one_car = seats = None
    for pax in range(1, 13):
        try:
            row = call_tour_option_rate(
                tour_id=tour_id,
                option_id=option["optionId"],
                supplier_id=option.get("supplierId") or 0,
                travel_date=travel_date,
                adults=pax,
                transfer_id=3,
            )["result"][0]
        except Exception:
            break
        rate = next(
            (
                float(t.get("startingFromRate") or 0)
                for t in row.get("initialTransferRates") or []
                if t.get("transferTypeId") == 2
            ),
            0.0,
        )
        if rate <= 0:
            break
        if one_car is None:
            one_car = rate
        elif rate > one_car:
            seats = pax - 1
            break
    if one_car is None:
        return None
    if seats is None:
        return None

    cars = -(-adults // seats)
    inr, _ = convert_supplier_price(one_car * cars, fare_currency="AED")
    if not inr:
        return None
    return {
        "transfer_type": "Private Transfer",
        "price_inr": inr,
        "price_display": f"₹{inr:,.0f}",
        "priced_for_pax": adults,
        "vehicles": cars,
        "seats_per_vehicle": seats,
    }


def tour_options(
    tour_id: int,
    travel_date: str,
    adults: int,
    child_ages: Sequence[int] = (),
    tour_name: str = "",
) -> dict[str, Any]:
    """Every bookable variant of one tour, priced for the whole party.

    Each row carries `prices`: the supplier's grand total for the party on
    every transfer type the option is sold with. Those totals are final --
    ticket, transfer and vehicles included -- so nothing is ever added to them.
    """
    ages = [int(a) for a in child_ages or ()]
    party = party_label(adults, ages)
    pax = adults + len(ages)
    listed = (
        call_tour_options(tour_id=tour_id, travel_date=travel_date)
        .get("result", {})
        .get("tourOptionlist")
        or []
    )

    def price(option: dict[str, Any]) -> dict[str, Any]:
        name = (option.get("optionName") or "").strip()
        row: dict[str, Any] = {
            "option_id": option.get("optionId"),
            "tour_id": tour_id,
            "supplier_id": option.get("supplierId"),
            "name": name,
            # The website heads its options page with the tour, then lists
            # the options under it; customers compare by both names.
            "display_name": f"{tour_name} — {name}" if tour_name and tour_name != name else name,
            "is_addon": "add-on" in name.lower(),
            "party": party,
            "timing": (
                "Fixed start times — call get_tour_timeslots for this option"
                if option.get("isTimeslot")
                else "Flexible, no fixed start time"
            ),
        }
        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            policies = ex.submit(option_policies, tour_id, option)
            prices = party_prices(tour_id, option, travel_date, adults, ages)
            row.update(policies.result())
        row.setdefault("cancellation", "Cancellation terms on request — confirm before booking")

        if not prices:
            row["price_display"] = "On request"
            # Over the supplier's own booking ceiling the ticket cannot be
            # priced in one booking, but the party still needs vehicles, so
            # quote the private transfer from the per-vehicle rate. Only then:
            # probing a dozen party sizes after any other refusal cost ~10s.
            if pax > _booking_ceiling(option):
                big = _private_for_large_party(tour_id, option, travel_date, pax)
                if big:
                    row["transfer_prices"] = [big]
            return row

        cheapest = prices[0]["total_inr"]
        row["prices"] = prices
        row["transfer_prices"] = prices  # back-compat name for the same totals
        if sold_per_unit(option):
            row["price_basis"] = "per_unit"
            row["prices_display"] = prices_display(prices, "1 unit")
            row["price_total_inr"] = cheapest
            row["price_display"] = f"{format_inr(cheapest)} per unit"
            row["price_note"] = (
                "Sold per unit (one vehicle or item), not per person. Ask how "
                "many units the party wants; each unit costs this price."
            )
            return row
        row["prices_display"] = prices_display(prices, party)
        row["price_total_inr"] = cheapest
        row["price_per_adult_inr"] = round(cheapest / max(1, pax), 2)
        row["price_total_display"] = f"{format_inr(cheapest)} total for {party}"

        # "Private Majlis - Per Group up to 6 Guests" is one majlis for the
        # party, not a per-head charge. The supplier still multiplies it by
        # pax, so we show what it bills and say the basis, rather than
        # dividing it down to a per-person figure it will not honour.
        if "per group" in name.lower():
            row["price_basis"] = "per_group"
            row["price_display"] = (
                f"{format_inr(cheapest)} for {party} — priced per group"
            )
            row["price_note"] = (
                "The supplier bills this group item once per guest in the "
                "party. Confirm the final charge at booking."
            )
        else:
            row["price_basis"] = "party_total"
            row["price_display"] = row["price_total_display"]
        return row

    with cf.ThreadPoolExecutor(max_workers=max(1, len(listed))) as ex:
        image = ex.submit(tour_image_url, tour_id, travel_date)
        options = list(ex.map(price, listed))

    # Variants share the tour's picture; the cards read it per row.
    url = image.result()
    if url:
        for o in options:
            o["image_url"] = url

    if not options:
        # An empty listing is not "no such tour": the same product is usually
        # sold under another tour id. Say where to look instead of letting
        # the agent wander through detail calls.
        return {
            "tour_id": tour_id,
            "tour_name": tour_name,
            "options": [],
            "agent_instructions": (
                "This tour id has no bookable options on that date. Call "
                f"search_tours(query={tour_name or 'the tour name'!r}, "
                "travel_date=...) and price the matching tour from that list; "
                "do not call get_tour_details for this id."
            ),
        }

    return {
        "tour_id": tour_id,
        "tour_name": tour_name,
        "adults": adults,
        "child_ages": ages,
        "party": party,
        "image_url": url,
        "options": options,
        "note": (
            f"Every figure in `prices` is the FINAL total for {party} on that "
            "transfer type: ticket, transfer and vehicles included, exactly what "
            "gujjutours.com charges. Quote it as it is, to the paisa. Never add "
            "a transfer to it, never add two of them together, and never "
            "multiply or divide it. A different party (more people, a child) "
            "means calling this tool again with that party."
        ),
    }
