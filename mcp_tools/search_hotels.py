"""search_hotels — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import (
    call_hotel_availability,
    call_hotel_property_info,
    call_hotel_static_data,
    discover_city_hotel_ids,
)
from core import TripPlannerError, nights_between
from mcp_tools.server import mcp
from parsers import parse_hotel_response
from reference_data_loader import (
    get_hotel_areas,
    get_hotel_ids_for_city,
    get_hotel_names,
    get_hotel_stars,
    resolve_city,
)

logger = logging.getLogger(__name__)

# How many live discovery hotel IDs to price per search. The supplier exposes
# thousands; the availability call can't price them all, so we send a batch
# (curated hotels first, then top star-matching discovery IDs).
_DISCOVERY_BATCH = 40


def _hotel_ids_to_search(
    city_id: int, city_key: str, min_stars: float, max_stars: float
) -> list[int]:
    """Curated IDs first (named/known), then a star-filtered batch of live
    discovery IDs — so the customer sees the real Dubai inventory, not just the
    two hardcoded hotels. Falls back to curated-only if discovery is unavailable."""
    curated = list(get_hotel_ids_for_city(city_key))
    discovered = discover_city_hotel_ids(city_id)  # ((id, stars), ...) star-desc
    if not discovered:
        return curated

    lo = min_stars if min_stars and min_stars > 0 else 0
    hi = max_stars if max_stars and max_stars > 0 else 5
    # Prefer hotels whose rating is in-band; rating 0 = unrated (keep as filler).
    in_band = [hid for hid, st in discovered if st <= 0 or lo <= st <= hi]

    seen: set[int] = set()
    out: list[int] = []
    for hid in [*curated, *in_band]:
        if hid not in seen:
            seen.add(hid)
            out.append(hid)
        if len(out) >= _DISCOVERY_BATCH:
            break
    return out


def _cancellation_display(option: dict[str, Any]) -> str:
    """One-line cancellation summary for the cheapest room.

    "Free cancellation until 28-11-2026" / "Non-refundable" / "Free until
    28-11-2026, then Rs 32,017". Empty string when the supplier told us nothing
    — better to say nothing than to guess at a customer's refund rights.
    """
    if option.get("has_free_cancellation"):
        base = "Free cancellation"
    else:
        base = ""
    rooms = option.get("rooms") or []
    if not isinstance(rooms, list) or not rooms:
        return base or ""
    first = rooms[0] if isinstance(rooms[0], dict) else {}
    terms = first.get("cancellation_policy") or []
    if not isinstance(terms, list) or not terms:
        return base or ""
    term = terms[0] if isinstance(terms[0], dict) else {}
    if term.get("is_nrf"):
        return "Non-refundable"
    deadline = str(term.get("to_date") or "").strip()
    fee = term.get("cancellation_price")
    if term.get("is_free_cancellation"):
        return f"Free cancellation until {deadline}" if deadline else "Free cancellation"
    # Not free: state the fee and, when known, the date it applies from.
    if isinstance(fee, (int, float)) and fee > 0:
        from core import format_inr

        if deadline:
            return f"Cancellation fee {format_inr(fee)} (from {deadline})"
        return f"Cancellation fee {format_inr(fee)}"
    return base or "Non-refundable"


def _resolve_hotel_by_name(name_query: str, city_id: int) -> int | None:
    """Resolve a hotel name to its ID using the fast public autocomplete API.

    Primary path: /api/core/v1/search/hotels — a single no-auth GET that returns
    the matching hotel's location_id in ~200ms (vs the old approach that fetched
    static data for 200 hotels just to fuzzy-match names).

    Falls back to the slow static-data word-match only if autocomplete returns nothing.
    """
    if not name_query:
        return None

    # Fast path — public autocomplete (no auth, ~200ms).
    # Always search "name + city name" so the autocomplete ranks local results
    # first and avoids matching same-brand hotels in other countries
    # (e.g. "Howard Johnson Bakersfield" instead of Dubai).
    from booking_api import call_entity_search
    from reference_data_loader import resolve_city as _rc
    _city_name = ""
    try:
        for _name in ("Dubai", "Abu Dhabi", "Sharjah"):
            _c = _rc(_name)
            if _c and int(_c.get("city_id", 0)) == city_id:
                _city_name = _name
                break
    except Exception:
        pass
    bare = name_query.strip()
    queries = [f"{bare} {_city_name}".strip(), bare] if _city_name else [bare]
    try:
        for q in queries:
            raw = call_entity_search(service="hotels", query=q, size=10)
            items = raw.get("data") or []
            hotel_items = [
                i for i in items
                if isinstance(i, dict)
                and i.get("location_id", 0) > 0
                and i.get("type", "").lower() not in ("city", "high_level_region", "neighborhood")
            ]
            city_match = [i for i in hotel_items if i.get("cityId") == city_id]
            if city_match:
                return int(city_match[0]["location_id"])
    except Exception:
        pass

    # Slow fallback — fuzzy word-match via static data bulk fetch
    q = name_query.lower().strip()
    discovered = discover_city_hotel_ids(city_id)
    from booking_api import call_hotel_static_data
    from reference_data_loader import get_hotel_ids_for_city
    # Use the city we resolved above, not a hardcoded "dubai" — otherwise the
    # curated list for Dubai was searched no matter which city was requested.
    curated = list(get_hotel_ids_for_city((_city_name or "dubai").lower()))
    top_discovered = [hid for hid, _ in (discovered or [])[:200]]
    seen: set[int] = set()
    candidate_ids: list[int] = []
    for hid in [*curated, *top_discovered]:
        if hid not in seen:
            seen.add(hid)
            candidate_ids.append(hid)
    if not candidate_ids:
        return None
    try:
        raw = call_hotel_static_data(hotel_ids=candidate_ids)
    except Exception:
        return None
    from parsers import parse_hotel_static_data_response
    hotels = parse_hotel_static_data_response(raw)
    best_id: int | None = None
    best_score = 0
    for h in hotels:
        hid = h.get("hotel_id")
        hname = (h.get("hotel_name") or "").lower()
        if not hid or not hname:
            continue
        words = [w for w in q.split() if len(w) > 2]
        score = sum(1 for w in words if w in hname)
        if score > best_score:
            best_score = score
            best_id = int(hid)
    min_score = 1 if len(q.split()) <= 2 else 2
    return best_id if best_score >= min_score else None


def _fetch_hotel_static(hotel_ids: list[int]) -> dict[str, dict]:
    """{hotel_id_str: static_record} from GetHotelStaticDataOptimize.
    Used to populate names, lat/lng, address, images on search results.
    Best-effort: returns {} on any failure."""
    if not hotel_ids:
        return {}
    try:
        raw = call_hotel_static_data(hotel_ids=hotel_ids)
    except Exception as e:
        logger.warning("hotel static enrichment failed: %s", e)
        return {}
    from parsers import parse_hotel_static_data_response
    hotels = parse_hotel_static_data_response(raw)
    return {str(h["hotel_id"]): h for h in hotels if h.get("hotel_id")}


def _fetch_hotel_names(hotel_ids: list[int]) -> dict[str, str]:
    """Real {hotel_id: HotelName} — extracted from static data."""
    static = _fetch_hotel_static(hotel_ids)
    return {
        hid: rec["hotel_name"]
        for hid, rec in static.items()
        if rec.get("hotel_name") and not rec["hotel_name"].startswith("Hotel ")
    }


def _fetch_hotel_coords(hotel_ids: list[int]) -> dict[str, dict]:
    """{hotel_id_str: {lat, lng, address}} from the address endpoint.

    Uses gethotelstaticdatalistsuboptimize_v1_Address which returns:
        {hotelID, hotel_address, lat, long}
    This is the definitive source of hotel coordinates for transfer searches.
    """
    if not hotel_ids:
        return {}
    try:
        raw = call_hotel_property_info(hotel_ids=hotel_ids)
    except Exception as e:
        logger.warning("hotel coords fetch failed: %s", e)
        return {}
    from parsers import parse_hotel_static_data_response
    hotels = parse_hotel_static_data_response(raw)
    out: dict[str, dict] = {}
    for h in hotels:
        hid = h.get("hotel_id")
        if not hid:
            continue
        lat = h.get("latitude")
        lng = h.get("longitude")
        if lat is not None and lng is not None:
            out[str(hid)] = {
                "latitude": lat,
                "longitude": lng,
                "full_address": h.get("full_address") or "",
            }
    return out


def _fetch_amenities_text(hotel_ids: list[int]) -> dict[str, str]:
    """{hotel_id: combined description text} from GetPropertyDescriptions, so we
    can check which requested amenities (pool/bar/spa) a hotel actually has.
    Best-effort: returns {} on failure (the caller then can't confirm amenities,
    which is correct — better than guessing)."""
    if not hotel_ids:
        return {}
    from booking_api import call_hotel_descriptions
    from parsers import parse_hotel_descriptions_response

    out: dict[str, str] = {}
    # GetPropertyDescriptions only returns data for ONE hotel id per call — a
    # batched id list comes back empty. So fetch per hotel (cap to keep latency
    # sane on the result set we actually show).
    for hid in hotel_ids[:8]:
        try:
            raw = call_hotel_descriptions(hotel_ids=[hid])
            sections = parse_hotel_descriptions_response(raw, max_results=50)
        except Exception as e:
            logger.warning("amenity enrichment failed for %s: %s", hid, e)
            continue
        parts = []
        for s in sections:
            if s.get("description"):
                parts.append(str(s["description"]))
            for sec in s.get("sections") or []:
                if sec.get("text"):
                    parts.append(str(sec["text"]))
        text = " ".join(parts).strip()
        if text:
            out[str(hid)] = text
    return out


def _impl(
    destination_city: str,
    check_in: str,
    check_out: str,
    adults: int = 2,
    children: int = 0,
    child_ages: list[int] | None = None,
    rooms: list[dict[str, Any]] | None = None,
    nationality: str = "India",
    min_stars: float = 0,
    max_stars: float = 5,
    max_results: int = 5,
    amenities: list[str] | None = None,
    hotel_name: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search hotels in the destination city. Returns options + per-night pricing.

    `hotel_name`: when the customer asks for a SPECIFIC hotel by name (e.g.
    "Howard Johnson", "Marriott", "Burj Al Arab"), pass the name here. The tool
    will resolve it to the exact hotel ID and search ONLY that hotel — so the
    customer gets a direct answer about availability and price for that property.
    If the named hotel is unavailable, the response will say so clearly instead
    of returning unrelated alternatives.

    `amenities`: optional list of must-have facility keywords the customer asked
    for, e.g. ["pool", "bar", "spa", "gym"]. When given, each returned hotel is
    enriched with `amenities_text` (the supplier's real description) and
    `amenities_matched` (which of the requested amenities the description actually
    mentions). This lets you confirm "has a pool/bar" from REAL data instead of
    guessing — never claim an amenity that isn't in amenities_matched.

    Occupancy — two ways to specify:
    - For a single room, pass flat `adults` / `children` / `child_ages`.
    - For multiple rooms (e.g. a family of 4 split as 2+2, or 3+1), pass
      `rooms` as a list of per-room dicts, each with `adults`, `children`,
      and `child_ages`. Example for 4 adults + 2 kids (ages 5, 8) in 2 rooms:
        rooms=[{"adults": 2, "children": 1, "child_ages": [5]},
               {"adults": 2, "children": 1, "child_ages": [8]}]
      When `rooms` is given it takes precedence over the flat args. ALWAYS
      confirm the room split with the customer before searching — never guess
      how many rooms 4+ guests want.
    """
    try:
        city = resolve_city(destination_city)
        if city is None or not city.get("city_id"):
            return {
                "error": True,
                "message": f"Unsupported hotel destination: {destination_city!r}",
                "error_type": "UnsupportedRoute",
            }
        city_id = int(city["city_id"])
        city_key = city["name"].lower()

        # Specific hotel requested by name — resolve to ID and search only that hotel
        specific_hotel_id: int | None = None
        if hotel_name and hotel_name.strip():
            specific_hotel_id = _resolve_hotel_by_name(hotel_name.strip(), city_id)
            if specific_hotel_id:
                hotel_ids = [specific_hotel_id]
            else:
                return {
                    "error": True,
                    "message": (
                        f"No property matching '{hotel_name}' is bookable in "
                        f"{city['name']} for these dates. This does NOT mean the "
                        f"hotel does not exist — tell the customer we can't book "
                        f"it for these dates and offer to check nearby options or "
                        f"different dates. Do not claim the hotel is in another "
                        f"country."
                    ),
                    "error_type": "HotelNotFound",
                    "hotel_name_searched": hotel_name,
                }
        else:
            # Curated hotels + a batch of LIVE discovery IDs (real Dubai inventory),
            # not just the two hardcoded reference hotels.
            hotel_ids = _hotel_ids_to_search(city_id, city_key, min_stars, max_stars)
        if not hotel_ids:
            return {
                "error": True,
                "message": (
                    f"No hotels found for {city['name']} (no curated IDs and live "
                    "discovery returned nothing)."
                ),
                "error_type": "MissingReferenceData",
            }
        nights = nights_between(check_in, check_out)
        room_count = len(rooms) if rooms else 1
        raw = call_hotel_availability(
            hotel_ids=hotel_ids,
            city_id=city_id,
            check_in=check_in,
            check_out=check_out,
            rooms=rooms,
            adults=adults,
            children=children,
            child_ages=child_ages,
            nationality=nationality,
            star_min=int(min_stars) if min_stars > 0 else 1,
            star_max=int(max_stars) if max_stars > 0 else 5,
        )
        # Stars: curated file first, then live discovery ratings (so discovered
        # hotels show their real star rating, not 0).
        star_map: dict[str, float] = {
            str(hid): st for hid, st in discover_city_hotel_ids(city_id) if st > 0
        }
        star_map.update(get_hotel_stars(city_key))  # curated wins on overlap
        # Static data: names, lat/lng, addresses, images for all searched IDs.
        static_map = _fetch_hotel_static(hotel_ids)
        name_map: dict[str, str] = {
            hid: rec["hotel_name"]
            for hid, rec in static_map.items()
            if rec.get("hotel_name") and not rec["hotel_name"].startswith("Hotel ")
        }
        name_map.update(get_hotel_names(city_key))
        options = parse_hotel_response(
            raw,
            nights=nights,
            hotel_names=name_map,
            hotel_areas=get_hotel_areas(city_key),
            hotel_stars=star_map,
            max_results=max_results * 2,
        )

        # Specific hotel searched but not available — say so clearly
        if specific_hotel_id and not options:
            return {
                "error": False,
                "available": False,
                "message": f"'{hotel_name}' (hotel ID {specific_hotel_id}) is not available for {check_in} to {check_out}. No rooms found for those dates. Suggest trying different dates.",
                "hotel_name_searched": hotel_name,
                "hotel_id": specific_hotel_id,
                "options": [],
                "total_results": 0,
            }
        # Filter by stars, but NEVER drop a hotel whose rating is unknown (0):
        # the availability API omits stars, so an unknown rating must not be
        # treated as "below min" — that silently zeroed out all results before.
        before_star_filter = list(options)
        filtered = [
            o for o in options if o.stars <= 0 or min_stars <= o.stars <= max_stars
        ][:max_results]

        # If the star filter emptied a non-empty result set, DON'T return 0 —
        # the contracted inventory is small (only 3-star hotels today), so a
        # min_stars>=4 request would otherwise strand the customer. Fall back to
        # the available hotels and flag that they're below the requested tier so
        # the agent can say "we have 3-star options" instead of "none available".
        note = None
        if not filtered and before_star_filter:
            avail_stars = sorted({o.stars for o in before_star_filter if o.stars > 0})
            tiers = ", ".join(f"{int(s)}-star" for s in avail_stars) or "available"
            note = (
                f"No hotels matched {int(min_stars)}-{int(max_stars)} star, so showing "
                f"the {tiers} options we do have. Tell the customer these are "
                f"{tiers} (not the {int(min_stars)}-star they asked for), don't claim "
                "nothing is available."
            )
            options = before_star_filter[:max_results]
        else:
            options = filtered

        option_dicts = [o.model_dump() for o in options]

        # Flatten the cheapest room's cancellation terms. The full per-room
        # policy (dates + fee) was already returned inside rooms[], but it
        # collapsed to a has_free_cancellation boolean in the reply — so
        # "free until 28 Nov, then Rs 32,017" was available and never said.
        for o in option_dicts:
            o["cancellation_display"] = _cancellation_display(o)

        # Coordinate enrichment: fetch lat/lng/address from the address endpoint
        # so the agent can pass hotel_lat/hotel_lng directly to search_airport_transfer_dubai
        # without needing a separate lookup_entity call.
        result_ids = [int(o["hotel_id"]) for o in option_dicts]
        coords_map = _fetch_hotel_coords(result_ids)
        for o in option_dicts:
            coords = coords_map.get(str(o.get("hotel_id")))
            if coords:
                o["latitude"] = coords["latitude"]
                o["longitude"] = coords["longitude"]
                if not o.get("full_address") and coords.get("full_address"):
                    o["full_address"] = coords["full_address"]

        # Amenity enrichment: if the customer asked for specific facilities
        # (pool/bar/spa/...), fetch the REAL supplier descriptions for these
        # exact hotels and flag which requested amenities each one actually has.
        # This is the source of truth — the agent must not guess amenities.
        if amenities:
            wanted = [a.strip().lower() for a in amenities if a and a.strip()]
            ids = [int(o["hotel_id"]) for o in option_dicts if str(o.get("hotel_id", "")).isdigit()]
            desc_by_id = _fetch_amenities_text(ids)
            for o in option_dicts:
                text = desc_by_id.get(str(o.get("hotel_id")), "")
                o["amenities_text"] = text
                low = text.lower()
                o["amenities_matched"] = [w for w in wanted if w in low]
            # Sort hotels that match ALL requested amenities to the top.
            option_dicts.sort(key=lambda o: -len(o.get("amenities_matched", [])))

        return {
            "options": option_dicts,
            "cheapest_price_inr": options[0].price_inr if options else None,
            "nights": nights,
            "per_night_inr": options[0].per_night_inr if options else None,
            "room_count": room_count,
            "total_results": len(options),
            **({"note": note} if note else {}),
            "pricing_note": (
                f"Hotel prices are PER ROOM for the whole {nights}-night stay "
                f"({room_count} room(s) booked), NOT per person. price_inr is the "
                "room total; per_night_inr is per room per night. Do not divide by pax."
            ),
            "search_params": {
                "destination": destination_city,
                "check_in": check_in,
                "check_out": check_out,
                "adults": adults,
                "children": children,
                "rooms": rooms,
                "room_count": room_count,
            },
        }
    except TripPlannerError as e:
        logger.warning("search_hotels error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_hotels unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl

search_hotels_tool = tool(cache_impl("search_hotels")(_impl))
search_hotels_tool.name = "search_hotels"
mcp.tool(name="search_hotels")(_impl)
