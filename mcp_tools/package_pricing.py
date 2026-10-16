"""Live package price, built exactly the way gujjutours.com builds it.

The supplier's package APIs carry no parts and no price (packagerate answers
empty lists), so packages were shown with no price at all. The website reads
the package's parts from its page (tours and transfers per day, hotels in
priority order) and prices them live:

    hotel   = packagehotelv1, the first hotel in priority order that is
              available, cheapest room for the party, whole stay (AED)
    service = optionRate `rate` for each tour or transfer, in AED, asked the
              way the website asks: one adult (age 30), or one unit for a
              per-vehicle transfer
    total   = (hotel + every service) in AED, converted once with the
              supplier currency rule (AED fare, INR account: x sellingROE)

Verified 2026-10-16: "Dubai Budget Escape 3 Nights" (id 2), 23-26 Oct,
2 adults, 1 room = Rs 31,455.11, the website's figure to the paisa.

The website asks every tour for ONE adult even when the party is larger, so a
2-adult package carries one adult's tickets. That is reproduced (the quote
must equal the website's) and reported to the client; `pricing_note` says so.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import date, timedelta
from typing import Any

from core import format_inr

log = logging.getLogger(__name__)

_PAGE_TTL_SECS = 6 * 3600.0
_LOCK = threading.Lock()
_PAGES: OrderedDict[int, tuple[float, dict[str, Any] | None]] = OrderedDict()

PRICING_NOTE = (
    "Priced live the way gujjutours.com prices this package: the hotel for "
    "the whole stay plus each tour and transfer in it."
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def package_page_path(*, package_id: int, name: str, country: str, country_id: int,
                      city: str, city_id: int) -> str:
    from booking_api.endpoints import PACKAGE_PAGE_PATH

    return PACKAGE_PAGE_PATH.format(country=_slug(country), country_id=int(country_id),
                                    city=_slug(city), city_id=int(city_id),
                                    name=_slug(name), package_id=int(package_id))


def package_parts(**where: Any) -> dict[str, Any] | None:
    """The package's tours, transfers and hotels from its website page."""
    pid = int(where["package_id"])
    now = time.monotonic()
    with _LOCK:
        hit = _PAGES.get(pid)
        if hit and now - hit[0] < _PAGE_TTL_SECS:
            return hit[1]

    from booking_api.endpoints import call_package_page
    from parsers import parse_package_page

    try:
        parts = parse_package_page(call_package_page(path=package_page_path(**where)))
    except Exception as e:  # noqa: BLE001 -- no parts means no price, not an error
        log.warning("package_page_failed id=%s: %s", pid, e)
        return None
    if parts and not parts["services"] and not parts["hotels"]:
        parts = None
    with _LOCK:
        _PAGES[pid] = (now, parts)
        while len(_PAGES) > 300:
            _PAGES.popitem(last=False)
    return parts


def _rooms(adults: int, child_ages: list[int], rooms: int) -> list[dict[str, Any]]:
    """Spread the party over the rooms the way a desk would: adults evenly,
    children with the first rooms."""
    rooms = max(1, min(int(rooms or 1), max(1, adults)))
    out = [{"adults": adults // rooms + (1 if i < adults % rooms else 0), "child_ages": []}
           for i in range(rooms)]
    for i, age in enumerate(child_ages):
        out[i % rooms]["child_ages"].append(int(age))
    return out


def _option_meta(tour_id: int, option_id: int, travel_date: str) -> tuple[int, bool]:
    """(supplier id, priced per person AND per vehicle) for one tour option.

    Both come from the option list, as the website reads them:
    - the supplier: Desert Safari 30647 belongs to supplier 2, and asked as
      supplier 1 it prices AED 474.10 instead of the website's 914.10;
    - `restrictedRateType` "Unit": the rate is then asked for one adult plus
      one unit (Desert Safari 53632 dune buggy: AED 34.10 alone, 102.30 with
      the unit).
    """
    from booking_api.endpoints import call_tour_options

    try:
        raw = call_tour_options(tour_id=tour_id, travel_date=travel_date)
    except Exception:  # noqa: BLE001 -- unknown means "supplier 1, per person"
        return 1, False
    for op in ((raw or {}).get("result") or {}).get("tourOptionlist") or []:
        if int(op.get("optionId") or 0) == int(option_id):
            names = {str(r.get("name") or "").lower() for r in op.get("restrictedRateType") or []}
            return int(op.get("supplierId") or 1), "unit" in names
    return 1, False


def _service_rate(svc: dict[str, Any], travel_date: str) -> tuple[float | None, str]:
    from booking_api.endpoints import call_tour_option_rate

    unit = svc["transfer_type_id"] == 4
    supplier, per_unit = _option_meta(svc["tour_id"], svc["option_id"], travel_date)
    extra = 0 if unit else int(per_unit)
    try:
        raw = call_tour_option_rate(
            tour_id=svc["tour_id"], option_id=svc["option_id"],
            supplier_id=supplier, travel_date=travel_date,
            adults=1, transfer_id=svc["transfer_type_id"] or 3, units=1 if unit else 0,
            extra_units=extra,
        )
        row = ((raw or {}).get("result") or [{}])[0] or {}
        # A private transfer on a tour with named vehicles is priced with the
        # vehicle's id; without it the supplier returns the ticket alone
        # (Dubai Frame private: AED 47.586 alone, 240.196 with vehicle 4, the
        # website's figure).
        if svc["transfer_type_id"] == 2:
            from mcp_tools.tour_pricing import _vehicle_for

            vehicle = _vehicle_for(row, 1)
            if vehicle:
                raw = call_tour_option_rate(
                    tour_id=svc["tour_id"], option_id=svc["option_id"],
                    supplier_id=supplier, travel_date=travel_date,
                    adults=1, transfer_id=2, extra_units=extra, vehicle_id=vehicle,
                   
                )
                row = ((raw or {}).get("result") or [{}])[0] or {}
        rate = float(row.get("rate") or 0)
        return (rate if rate > 0 else None), str(row.get("currencyCode") or "AED")
    except Exception as e:  # noqa: BLE001 -- an unpriced part is left out, as on the website
        log.warning("package_service_rate_failed tour=%s option=%s: %s",
                    svc.get("tour_id"), svc.get("option_id"), e)
        return None, "AED"


def price_package(
    *,
    package_id: int,
    name: str,
    country: str,
    country_id: int,
    city: str,
    city_id: int,
    check_in: str,
    nights: int,
    adults: int,
    child_ages: list[int] | None = None,
    rooms: int = 1,
    nationality: str = "India",
) -> dict[str, Any] | None:
    """The package's live total for this party and date, with its parts.

    None when the parts cannot be read. A result with `price_inr` None means
    a part could not be priced; the parts are still listed.
    """
    from booking_api.endpoints import call_package_hotels
    from fx import convert_supplier_price
    from parsers import parse_package_hotel_rates

    parts = package_parts(package_id=package_id, name=name, country=country,
                          country_id=country_id, city=city, city_id=city_id)
    if not parts:
        return None
    nights = int(nights or parts.get("nights") or 0)
    start = date.fromisoformat(check_in)
    check_out = (start + timedelta(days=nights)).isoformat()
    ages = [int(a) for a in child_ages or []]
    room_list = _rooms(int(adults), ages, rooms)

    def day_date(day: int) -> str:
        return (start + timedelta(days=max(0, day - 1))).isoformat()

    # Optional parts are priced in too: the website counts Burj Khalifa
    # (marked optional in Marina Cruise Escape) in its total.
    services = list(parts["services"])
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        hotel_job = pool.submit(
            call_package_hotels, city_id=city_id, country_id=country_id, check_in=check_in,
            check_out=check_out, hotel_ids=[h["hotel_id"] for h in parts["hotels"]],
            rooms=room_list, nationality=nationality,
        ) if parts["hotels"] else None
        rate_jobs = [pool.submit(_service_rate, s, day_date(s["day"])) for s in services]
        try:
            hotel_rates = parse_package_hotel_rates(hotel_job.result()) if hotel_job else {}
        except Exception as e:  # noqa: BLE001
            log.warning("package_hotels_failed id=%s: %s", package_id, e)
            hotel_rates = {}
        rates = [j.result() for j in rate_jobs]

    hotel = None
    for h in parts["hotels"]:
        if h["hotel_id"] in hotel_rates:
            hotel = {**h, **hotel_rates[h["hotel_id"]]}
            break

    def inr(aed: float | None) -> float | None:
        if not aed:
            return None
        value, _ = convert_supplier_price(aed, fare_currency="AED", places=2)
        return value

    lines: list[dict[str, Any]] = []
    total_aed = 0.0
    complete = True
    if parts["hotels"]:
        if hotel:
            total_aed += hotel["price"]
            lines.append({"kind": "hotel", "name": hotel["name"], "room": hotel["room"],
                          "meal": hotel["meal"], "nights": nights,
                          "price_inr": inr(hotel["price"])})
        else:
            complete = False
    unpriced: list[str] = []
    for svc, (rate, _cur) in zip(services, rates, strict=True):
        # A part the supplier will not price is left out of the total, as the
        # website does (Classic Dubai's dhow cruise, tour 70, has no rate and
        # the website total omits it); it is named so nobody reads it as free.
        if rate is None:
            unpriced.append(svc["name"])
        else:
            total_aed += rate
        lines.append({"kind": "transfer" if svc["transfer_type_id"] == 4 else "tour",
                      "day": svc["day"], "date": day_date(svc["day"]), "name": svc["name"],
                      "option": svc["option_name"], "transfer": svc["transfer_type"],
                      "optional": svc["optional"], "priced": rate is not None,
                      "price_inr": inr(rate)})

    total = inr(total_aed) if complete and total_aed > 0 else None
    party = int(adults) + len(ages)
    # Per person from the unrounded total, as the website divides it:
    # Rs 31,455.11 for 2 shows Rs 15,727.55 each, not 15,727.56.
    per_person = inr(total_aed / party) if total and party else None

    return {
        "package_id": int(package_id),
        "check_in": check_in,
        "check_out": check_out,
        "nights": nights,
        "rooms": len(room_list),
        "hotel": ({"name": hotel["name"], "stars": hotel["stars"], "address": hotel["address"],
                   "room": hotel["room"], "meal": hotel["meal"], "refundable": hotel["refundable"],
                   "image_url": hotel["image_url"]} if hotel else None),
        "hotel_choices": [h["name"] for h in parts["hotels"]],
        "lines": lines,
        "price_inr": total,
        "price_display": format_inr(total) if total else None,
        "price_per_person_inr": per_person,
        "price_per_person_display": format_inr(per_person) if per_person else None,
        "pricing_available": bool(total),
        "unpriced_parts": unpriced,
        "pricing_note": (
            (PRICING_NOTE + (" Not priced by the supplier for these dates, so not in the "
                             "total: " + ", ".join(unpriced) + "." if unpriced else ""))
            if total else
            "The hotel for this package is not available for these dates, so no total is shown."
        ),
    }
