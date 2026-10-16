"""Price and place the parts of a planned trip the way GujjuTours sells them.

plan_itinerary_tool lays out the days; this module fills in what the customer
actually pays for and where they eat:

- each tour's exact supplier total for the party, with the transfer type the
  customer needs (sharing pickup from the hotel where the tour offers it),
  instead of a per-adult price multiplied in code (Rs 1,180.26 shown against
  the supplier's Rs 1,180.25);
- the airport pickup and drop-off: GujjuTours' own airport transfer tours,
  priced per vehicle, for the zone the hotel is in and a vehicle the party
  fits in;
- a venue for each lunch and dinner: one of our restaurants near the activity
  before it and open at that hour, else the hotel's restaurant.

Every figure comes from a supplier call; nothing is estimated. A part that
cannot be priced is returned without a price, never with a guess.
"""

from __future__ import annotations

import json
import logging
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_REF = Path(__file__).resolve().parent.parent / "reference_data"
# A restaurant is "near" an activity within this distance.
NEAR_KM = 6.0


# --------------------------------------------------------------------- places
@lru_cache(maxsize=1)
def _attractions() -> list[dict[str, Any]]:
    with (_REF / "dubai_attractions.json").open(encoding="utf-8") as f:
        return list(json.load(f).get("attractions") or [])


@lru_cache(maxsize=1)
def _area_centres() -> dict[str, tuple[float, float]]:
    with (_REF / "dubai_areas.json").open(encoding="utf-8") as f:
        areas = json.load(f).get("areas") or []
    return {str(a["area"]).lower(): (float(a["lat"]), float(a["lng"])) for a in areas}


def place_of(activity: str) -> dict[str, Any] | None:
    """Where an activity happens: {area, lat, lng, remote}, or None."""
    name = (activity or "").lower()
    for entry in _attractions():
        if any(m in name for m in entry.get("match") or []):
            area = str(entry.get("area") or "")
            if area and area.lower() in _area_centres():
                lat, lng = _area_centres()[area.lower()]
            else:
                lat, lng = entry.get("lat"), entry.get("lng")
            if lat is None:
                return None
            return {"area": area, "lat": float(lat), "lng": float(lng),
                    "remote": bool(entry.get("remote"))}
    return None


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------- meals
_HOURS = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)\s*-\s*(\d{1,2}):(\d{2})\s*([AP]M)", re.I)


def _to_min(h: str, m: str, ap: str) -> int:
    hour = int(h) % 12 + (12 if ap.upper() == "PM" else 0)
    return hour * 60 + int(m)


def open_at(opening_hours_display: str, hhmm: str) -> bool:
    """True when the restaurant is open at `hhmm`; unknown hours count as open."""
    spans = _HOURS.findall(opening_hours_display or "")
    if not spans or not hhmm:
        return True
    want = int(hhmm[:2]) * 60 + int(hhmm[3:5])
    for h1, m1, a1, h2, m2, a2 in spans:
        start, end = _to_min(h1, m1, a1), _to_min(h2, m2, a2)
        if end <= start:  # closes after midnight ("10:00 AM-12:00 AM")
            end += 24 * 60
        if start <= want <= end - 45:
            return True
    return False


def _restaurants(date: str, adults: int) -> list[dict[str, Any]]:
    try:
        from mcp_tools.search_restaurants import _impl as search

        return list((search(destination_city="Dubai", search_date=date, adults=adults,
                            max_results=50) or {}).get("options") or [])
    except Exception as e:
        log.warning("plan_restaurants_failed: %s", e)
        return []


def meal_venue(*, near: str, at: str, date: str, adults: int, hotel_name: str,
               hotel_lat: float | None = None, hotel_lng: float | None = None) -> dict[str, Any]:
    """Where a lunch or dinner is eaten.

    One of our restaurants within NEAR_KM of the activity before it (or of the
    hotel when the meal follows no activity), open at that hour; else the
    hotel's restaurant. Restaurant prices are a per-person starting price, so
    they are shown as such and kept out of the trip total.
    """
    place = place_of(near) if near else None
    if place and place.get("remote"):
        return {"venue": f"at {near}", "kind": "on_site",
                "note": "Food is available on site"}
    origin = (place["lat"], place["lng"]) if place else (
        (hotel_lat, hotel_lng) if hotel_lat is not None and hotel_lng is not None else None)
    best = None
    if origin is not None:
        for r in _restaurants(date, adults):
            lat, lng = r.get("latitude"), r.get("longitude")
            if lat is None or lng is None or not open_at(str(r.get("opening_hours_display") or ""), at):
                continue
            d = _km(origin, (float(lat), float(lng)))
            if d <= NEAR_KM and (best is None or d < best[0]):
                best = (d, r)
    if best:
        d, r = best
        return {
            "venue": str(r.get("name") or ""),
            "kind": "restaurant",
            "restaurant_id": r.get("restaurant_id"),
            "distance_km": round(d, 1),
            "price_from_per_person_inr": r.get("price_per_adult_inr"),
            "price_display": r.get("price_display") or r.get("price_from_display"),
            "cuisine": r.get("cuisine"),
            "veg_type": r.get("veg_type"),
            "note": "Pay at the restaurant; price from, per person",
        }
    return {"venue": f"{hotel_name} restaurant" if hotel_name else "the hotel restaurant",
            "kind": "hotel", "note": "Pay at the hotel"}


# ---------------------------------------------------------------------- tours
_SEATS = re.compile(r"(\d+)\s*seater", re.I)
_TRANSFER_PREFERENCE = ("sharing transfer", "per vehicle", "without transfer", "private transfer")


@lru_cache(maxsize=256)
def _tour_options(tour_id: int, date: str) -> tuple[dict[str, Any], ...]:
    """The tour's bookable options for a day, in the supplier's order (one call)."""
    from booking_api.endpoints import call_tour_options

    try:
        raw = call_tour_options(tour_id=int(tour_id), travel_date=date)
    except Exception as e:
        log.warning("plan_tour_options_failed id=%s: %s", tour_id, e)
        return ()
    return tuple(o for o in ((raw or {}).get("result") or {}).get("tourOptionlist") or [] if isinstance(o, dict))


_PRICE_CACHE: dict[tuple[int, str, int, tuple[int, ...]], dict[str, Any] | None] = {}


def tour_price(*, tour_id: int, name: str, date: str, adults: int,
               child_ages: list[int] | None = None) -> dict[str, Any] | None:
    """The supplier's total for the party for one tour on one day.

    The tour's standard option (the first in the supplier's order that offers
    a sharing transfer, else the first), priced ONLY for the transfer the
    customer needs: sharing pickup from the hotel where offered, else the
    ticket alone or per vehicle. Pricing every option and transfer type made a
    4-night plan send ~750 rate calls and take 8 minutes.
    {total_inr, option, transfer_type, includes_dinner} or None.
    """
    from mcp_tools.tour_pricing import offered_transfers, party_prices

    ages = tuple(int(a) for a in child_ages or ())
    key = (int(tour_id), date, int(adults), ages)
    if key in _PRICE_CACHE:
        return _PRICE_CACHE[key]
    options = [o for o in _tour_options(int(tour_id), date)
               if "add-on" not in str(o.get("optionName") or "").lower()
               and "add on" not in str(o.get("optionName") or "").lower()]
    result = None
    if options:
        sharing = [o for o in options if any(t == 1 for t, _n in offered_transfers(o))]
        chosen = (sharing or options)[0]
        kinds = offered_transfers(chosen)
        want = next((k for k in kinds if k[0] == 1), None) or next((k for k in kinds if k[0] == 3), None)             or (kinds[0] if kinds else None)
        option = dict(chosen)
        if want:
            # Price just that transfer type.
            option["validateTourOption"] = [v for v in chosen.get("validateTourOption") or []
                                            if v.get("transferTypeId") == want[0]]
        try:
            prices = party_prices(int(tour_id), option, date, int(adults), list(ages))
        except Exception as e:
            log.warning("plan_tour_rate_failed id=%s: %s", tour_id, e)
            prices = []
        if prices:
            p = prices[0]
            text = f"{name} {chosen.get('optionName') or ''}".lower()
            result = {
                "total_inr": float(p.get("total_inr") or 0),
                "total_display": p.get("total_display"),
                "transfer_type": p.get("transfer_type"),
                "option": chosen.get("optionName"),
                "option_id": chosen.get("optionId"),
                "supplier_id": chosen.get("supplierId"),
                "includes_dinner": any(w in text for w in ("dinner", "bbq", "barbecue", "buffet")),
            }
    _PRICE_CACHE[key] = result
    if len(_PRICE_CACHE) > 2000:
        _PRICE_CACHE.clear()
    return result


# ------------------------------------------------------------------ transfers
def _zone_text(tour_id: int, option: dict[str, Any]) -> str:
    from booking_api.endpoints import call_tour_option_description
    from parsers import parse_tour_option_description

    try:
        sections = parse_tour_option_description(call_tour_option_description(
            tour_id=tour_id, tour_option_id=option["option_id"], supplier_id=int(option.get("supplier_id") or 1)))
    except Exception:
        return ""
    return " ".join(i for s in sections for i in s["items"]).lower()


@lru_cache(maxsize=64)
def _zone_texts(tour_id: int, options_key: str) -> dict[str, str]:
    options = json.loads(options_key)
    return {str(o["option_id"]): _zone_text(tour_id, o) for o in options}


def _area_names(*areas: str) -> list[str]:
    """An area's names as zone lists might write them: its name, its aliases,
    and the name without "Al" (Barsha Heights is listed as "TECOM")."""
    with (_REF / "dubai_areas.json").open(encoding="utf-8") as f:
        known = {str(a["area"]).lower(): a for a in json.load(f).get("areas") or []}
    out: list[str] = []
    for area in areas:
        a = (area or "").strip().lower()
        if not a:
            continue
        for n in [a, *[str(x).lower() for x in (known.get(a) or {}).get("aliases") or []]]:
            for v in (n, n[3:] if n.startswith("al ") else n):
                if v and v not in out:
                    out.append(v)
    return out


def airport_transfer(*, direction: str, date: str, adults: int, hotel_area: str,
                     hotel_district: str = "") -> dict[str, Any] | None:
    """GujjuTours' airport pickup or drop-off for the hotel's zone, priced.

    `direction` is "pickup" or "dropoff". The zone is the option whose
    description lists the hotel's area (Zone 1: Deira / Bur Dubai / ...); the
    vehicle is the smallest that seats the party. Priced per vehicle, as the
    website sells it. None when no zone lists the area.
    """
    from mcp_tools.get_tour_options import _cached
    from mcp_tools.search_tours import _impl as search_tours

    want = "pickup" if direction == "pickup" else "dropoff"
    try:
        found = search_tours(destination_city="Dubai", travel_date=date, adults=max(1, adults),
                             query=f"Airport {'Pickup' if want == 'pickup' else 'Dropoff'}")
    except Exception as e:
        log.warning("plan_airport_search_failed: %s", e)
        return None
    tour = next((o for o in (found or {}).get("options") or []
                 if o.get("sold_per_vehicle") and want in str(o.get("name") or "").lower().replace("-", "")
                 .replace(" ", "")), None)
    if not tour:
        return None
    tour_id = int(tour["tour_id"])
    try:
        options = [o for o in (_cached(tour_id=tour_id, travel_date=date, adults=max(1, adults),
                                       child_ages=[], tour_name=str(tour["name"])) or {})
                   .get("options") or [] if o.get("prices")]
    except Exception as e:
        log.warning("plan_airport_options_failed: %s", e)
        return None
    if not options:
        return None
    texts = _zone_texts(tour_id, json.dumps(
        [{"option_id": o["option_id"], "supplier_id": o.get("supplier_id")} for o in options], sort_keys=True))
    names = _area_names(hotel_area, hotel_district)

    def in_zone(o: dict[str, Any]) -> bool:
        text = texts.get(str(o["option_id"]), "")
        return any(re.search(r"\b" + re.escape(n) + r"\b", text) for n in names)

    def seats(o: dict[str, Any]) -> int:
        m = _SEATS.search(str(o.get("name") or ""))
        return int(m.group(1)) if m else 0

    fitting = [o for o in options if in_zone(o) and seats(o) >= adults]
    if not fitting:
        return None
    best = min(fitting, key=lambda o: (seats(o), float(o["prices"][0].get("total_inr") or 0)))
    price = best["prices"][0]
    return {
        "name": str(tour["name"]),
        "option": best.get("name"),
        "total_inr": float(price.get("total_inr") or 0),
        "total_display": price.get("total_display"),
    }
