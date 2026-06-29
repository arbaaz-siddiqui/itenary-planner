"""search_hotels — agent + MCP tool."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import (
    call_hotel_availability,
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


def _resolve_hotel_by_name(name_query: str, city_id: int) -> int | None:
    """Fuzzy-match a hotel name to its ID using the live city inventory.
    Returns the best-matching hotel_id or None if no confident match found."""
    if not name_query:
        return None
    q = name_query.lower().strip()
    discovered = discover_city_hotel_ids(city_id)
    from booking_api import call_hotel_static_data
    from reference_data_loader import get_hotel_ids_for_city
    city_key = q  # rough fallback; actual city_key not needed just for curated IDs
    # Always include curated IDs + top 200 discovery (curated first so known hotels match)
    curated = list(get_hotel_ids_for_city("dubai"))
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
    info = raw.get("PropertyInfo") if isinstance(raw, dict) else None
    if not isinstance(info, list):
        return None
    best_id: int | None = None
    best_score = 0
    for h in info:
        if not isinstance(h, dict):
            continue
        hid = h.get("hotelID") or h.get("HotelId") or h.get("hotelId")
        hname = (h.get("HotelName") or h.get("hotelName") or "").lower()
        if not hid or not hname:
            continue
        # Score: count how many query words appear in hotel name
        words = [w for w in q.split() if len(w) > 2]
        score = sum(1 for w in words if w in hname)
        if score > best_score:
            best_score = score
            best_id = int(hid)
    # Require at least 2 matching words (or 1 if query is a single word)
    min_score = 1 if len(q.split()) <= 2 else 2
    return best_id if best_score >= min_score else None


def _fetch_hotel_names(hotel_ids: list[int]) -> dict[str, str]:
    """Real {hotel_id: HotelName} from GetHotelStaticDataOptimize, so discovered
    hotels show their actual name (e.g. "Mövenpick Dubai Creek") instead of the
    "Hotel <id>" fallback. Best-effort: returns {} on any failure (the parser
    then keeps the "Hotel <id>" placeholder rather than erroring)."""
    if not hotel_ids:
        return {}
    try:
        raw = call_hotel_static_data(hotel_ids=hotel_ids)
    except Exception as e:
        logger.warning("hotel name enrichment failed: %s", e)
        return {}
    info = raw.get("PropertyInfo") if isinstance(raw, dict) else None
    if not isinstance(info, list):
        return {}
    names: dict[str, str] = {}
    for h in info:
        if not isinstance(h, dict):
            continue
        hid = h.get("hotelID") or h.get("HotelId") or h.get("hotelId")
        name = h.get("HotelName") or h.get("hotelName")
        if hid is not None and name:
            names[str(hid)] = str(name).strip()
    return names


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
        text = " ".join(str(s.get("description", "")) for s in sections).strip()
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
                    "message": f"Could not find a hotel matching '{hotel_name}' in {city['name']}. Try a different name or search without specifying a hotel.",
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
        # Names: real supplier names for the searched IDs (so discovered hotels
        # show "Mövenpick Dubai Creek", not "Hotel 217"), with the curated file
        # overriding for the hand-named hotels.
        name_map: dict[str, str] = _fetch_hotel_names(hotel_ids)
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


search_hotels_tool = tool(_impl)
search_hotels_tool.name = "search_hotels"
mcp.tool(name="search_hotels")(_impl)
