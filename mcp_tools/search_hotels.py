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

# Hotel content fan-out budget: one description call per hotel, run wide and
# time-boxed so a slow supplier can never stall a hotel search.
_CONTENT_MAX_WORKERS = 12
_CONTENT_DEADLINE_S = 6.0

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


# Keywords worth pulling out of the supplier's long description, grouped so we
# can tell a customer what the hotel actually HAS. The description is prose
# ("an outdoor pool, a sauna, and a 24-hour fitness center"), so the agent could
# not reliably state amenities from the search result — it had none at all.
_AMENITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Pool": ("outdoor pool", "indoor pool", "swimming pool", "rooftop pool"),
    "Gym": ("fitness cent", "fitness facilit", "gym"),
    "Spa": ("spa ", "massage", "body treatment", "facial"),
    "Sauna": ("sauna", "steam room"),
    "Free WiFi": ("complimentary wireless", "free wifi", "free wi-fi", "complimentary wi-fi"),
    "Restaurant": ("restaurant", "all day dining", "dining establishment"),
    # Plural "2 bars/lounges" is the supplier's usual phrasing and matched none
    # of the old keys, so a bar-filtered search listed hotels with no Bar shown.
    # Keys stay specific: bare "bar" would hit barber/barbecue.
    "Bar": ("bar/lounge", "bars/lounge", " bar,", " bars ", " bar ", "cocktail",
            "lounge", "pool bar", "rooftop bar"),
    "Breakfast available": ("breakfast",),
    "Room service": ("room service",),
    "Airport shuttle": ("airport shuttle", "airport transportation"),
    "Parking": ("valet parking", "free parking", "self parking"),
    "Business centre": ("business cent",),
    "Laundry": ("dry cleaning", "laundry"),
    "Family friendly": ("babysitting", "children's", "kids club"),
    "Beach access": ("private beach", "beach access"),
}


def _match_amenities(wanted: list[str], description: str) -> list[str]:
    """Which requested amenities the description really mentions.

    Word-boundary matched: plain substring search made "bar" hit "minibars"
    (an in-room fridge), so a bar-filtered search returned hotels without one.
    Where the amenity is a known label we reuse its keyword list, which also
    catches phrasings like "2 bars/lounges".
    """
    import re as _re

    text = (description or "").lower()
    if not text:
        return []
    by_label = {k.lower(): v for k, v in _AMENITY_KEYWORDS.items()}
    out: list[str] = []
    for w in wanted:
        # A key written with trailing space in _AMENITY_KEYWORDS (" bar ") is a
        # WHOLE WORD and gets both boundaries, so it cannot match minibar,
        # barber or barbecue. Any other key is a prefix ("fitness cent") and
        # gets a left boundary only. Plain substring search returned bar-less
        # hotels; a bare word boundary matched barber.
        raw_keys = [k for k in (by_label.get(w) or ()) if k.strip()]
        hit = False
        for k in [*raw_keys, f" {w} "]:
            p = _re.escape(k.strip())
            pattern = rf"\b{p}s?\b" if k.endswith(" ") else rf"\b{p}"
            if _re.search(pattern, text):
                hit = True
                break
        if hit:
            out.append(w)
    return out


def _extract_amenities(description: str) -> list[str]:
    """Pull a clean amenity list out of the supplier's prose description."""
    text = (description or "").lower()
    if not text:
        return []
    return [label for label, keys in _AMENITY_KEYWORDS.items() if any(k in text for k in keys)]


def _dining_summary(description: str) -> str:
    """The food story: "3 restaurants and a coffee shop", named venues, breakfast.

    Customers ask about food constantly and the search result carried nothing,
    so the agent either stayed silent or guessed.
    """
    import re as _re

    text = (description or "").strip()
    if not text:
        return ""
    bits: list[str] = []
    m = _re.search(r"(\d+)\s+restaurants?", text, _re.I)
    if m:
        bits.append(f"{m.group(1)} restaurants")
    for venue in _re.findall(r"at ([A-Z][A-Za-z0-9'\- ]{3,34}?(?:Dining|Restaurant|Lounge|Cafe|Bar|Grill|Kitchen))", text):
        bits.append(venue.strip())
    if _re.search(r"coffee shop|caf[eé]", text, _re.I):
        bits.append("coffee shop")
    if _re.search(r"buffet breakfast", text, _re.I):
        bits.append("buffet breakfast")
    elif _re.search(r"breakfast", text, _re.I):
        bits.append("breakfast available")
    # de-dupe, keep order
    seen: list[str] = []
    for b in bits:
        if b.lower() not in {x.lower() for x in seen}:
            seen.append(b)
    return " · ".join(seen[:4])


def _enrich_with_content(option_dicts: list[dict[str, Any]], city_id: int) -> None:
    """Attach amenities / dining / description to each hotel in the results.

    Why here and not left to `get_hotel_description`: the agent almost never made
    that extra call, so a whole conversation could go by without the customer
    hearing one thing about the property beyond its price. Doing it here makes
    the detail the default.

    ONE HOTEL PER CALL: GetPropertyDescriptions returns an EMPTY list when given
    several ids ([1350] -> 1 description, [1350, 1351] -> 0), and the response
    does not echo `hotel_id` back, so batching loses the mapping entirely. We
    therefore fan out one call per hotel and pair by position.

    Best-effort and time-boxed: on failure or timeout the hotels simply keep
    their price-only shape rather than the search failing.
    """
    import concurrent.futures as _cf

    if not option_dicts:
        return

    def _one(o: dict[str, Any]) -> None:
        hid = o.get("hotel_id")
        if not hid:
            return
        try:
            from booking_api import call_hotel_descriptions
            from parsers import parse_hotel_descriptions_response

            parsed = parse_hotel_descriptions_response(
                call_hotel_descriptions(hotel_ids=[hid], city_id=city_id)
            )
        except Exception as e:  # noqa: BLE001 — content is a bonus, never fatal
            logger.debug("hotel content fetch failed for %s: %s", hid, e)
            return
        if not parsed or not isinstance(parsed[0], dict):
            return
        desc = str(parsed[0].get("description") or "")
        if not desc:
            return
        o["amenities"] = _extract_amenities(desc)
        o["amenities_display"] = " · ".join(o["amenities"])
        o["dining_display"] = _dining_summary(desc)
        o["description_short"] = desc[:280].rsplit(" ", 1)[0] + ("…" if len(desc) > 280 else "")

    ex = _cf.ThreadPoolExecutor(max_workers=min(_CONTENT_MAX_WORKERS, len(option_dicts)))
    try:
        futures = [ex.submit(_one, o) for o in option_dicts]
        _cf.wait(futures, timeout=_CONTENT_DEADLINE_S)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _summarise_refundable(option: dict[str, Any], rooms: list[Any]) -> None:
    """Record whether a refundable room exists, and what the cheapest one costs.

    The search result carries ONE price per hotel — the cheapest offer — and its
    policy. When that offer is non-refundable the agent reported the whole hotel
    as non-refundable, which is wrong and was exactly the client's complaint:
    they asked for refundable options and were told none existed.
    """
    free: list[tuple[float, str]] = []
    total = 0
    for rm in rooms or []:
        if not isinstance(rm, dict):
            continue
        total += 1
        terms = rm.get("cancellation_policy") or []
        is_free = any(
            isinstance(t, dict) and t.get("is_free_cancellation") for t in terms
        )
        if is_free:
            try:
                price = float(rm.get("price_inr") or 0.0)
            except (TypeError, ValueError):
                price = 0.0
            if price > 0:
                free.append((price, str(rm.get("room_type_name") or "")))

    option["refundable_room_count"] = len(free)
    option["room_options_count"] = total
    if not free:
        option["refundable_display"] = "No refundable rate at this hotel for these dates"
        return
    free.sort()
    price, name = free[0]
    from core import format_inr

    shown = option.get("price_inr")
    same = isinstance(shown, (int, float)) and abs(float(shown) - price) < 1.0
    option["cheapest_refundable_inr"] = round(price, 2)
    option["cheapest_refundable_room"] = name
    option["refundable_display"] = (
        "The rate shown is refundable"
        if same
        else f"Refundable rooms from {format_inr(round(price))} ({len(free)} of {total} rates)"
    )


def _clean_room_name(raw: str) -> str:
    """Trim the supplier's duplicated bed text and the "- Non Refundable" suffix.

    Raw: "Standard Double Room, 1 King Bed 1 King Bed- Non Refundable"
    Out: "Standard Double Room, 1 King Bed"
    The policy is shown in its own column, so repeating it in the name is noise.
    """
    import re as _re

    name = str(raw or "").strip()
    # Drop a trailing "- Non Refundable" / "- Non Refu" (the supplier truncates
    # it) - the policy has its own column, so repeating it here is noise.
    name = _re.sub(r"[-–]\s*non[\s-]*refu\w*\s*$", "", name, flags=_re.I).strip(" -–,")
    # Collapse the duplicated bed phrase: "1 King Bed 1 King Bed" -> "1 King Bed".
    # Use [\w ]+? not \w+ because the phrase is two words, e.g. "King Bed".
    name = _re.sub(r"\b(\d+ [\w ]+?Beds?)\s+\1\b", r"\1", name, flags=_re.I)
    return _re.sub(r"\s{2,}", " ", name).strip()


def _room_policy_breakdown(rooms: list[Any], nights: int) -> list[dict[str, Any]]:
    """Per-room cancellation table so the customer can CHOOSE.

    The client's ask: show which rooms are refundable and which are not, side by
    side, rather than one verdict for the hotel. Sorted cheapest-first within
    refundable / non-refundable so the flexible options are easy to compare.

    Each entry: {room, board, price_inr, price_display, refundable,
    policy, free_until}.
    """
    from core import format_inr

    out: list[dict[str, Any]] = []
    for rm in rooms or []:
        if not isinstance(rm, dict):
            continue
        try:
            price = float(rm.get("price_inr") or 0.0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        terms = rm.get("cancellation_policy") or []
        term = terms[0] if terms and isinstance(terms[0], dict) else {}
        is_free = any(
            isinstance(t, dict) and t.get("is_free_cancellation") for t in terms
        )
        deadline = _human_date(str(term.get("to_date") or "").strip()) if is_free else ""
        if is_free:
            policy = f"Free cancellation until {deadline}" if deadline else "Free cancellation"
        elif term.get("is_nrf"):
            policy = "Non-refundable"
        else:
            fee = term.get("cancellation_price")
            policy = (
                f"Cancellation fee {format_inr(round(fee))}"
                if isinstance(fee, (int, float)) and fee > 0
                else "Non-refundable"
            )
        out.append(
            {
                "room": _clean_room_name(rm.get("room_type_name")),
                "board": str(rm.get("meal_name") or "Room Only"),
                "price_inr": round(price, 2),
                "price_display": format_inr(round(price)),
                "per_night_inr": round(price / nights, 2) if nights else None,
                "refundable": is_free,
                "policy": policy,
                "free_until": deadline,
            }
        )
    # Refundable first, then cheapest — the flexible options are what a customer
    # asking about cancellation is scanning for.
    out.sort(key=lambda r: (not r["refundable"], r["price_inr"]))
    return out


def _human_date(raw: str) -> str:
    """MM-DD-YYYY (supplier format) -> "16 Sep 2026".

    Relaying the raw string was actively misleading: "09-16-2026" reads as
    16 September to a UK/Indian customer only by luck, and "10-09-2026" is
    genuinely ambiguous. Spelling the month removes the doubt.
    """
    from datetime import datetime

    for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d %b %Y")
        except (ValueError, TypeError):
            continue
    return raw


def _cancellation_display(option: dict[str, Any]) -> str:
    """One-line cancellation summary for the cheapest room.

    "Free cancellation until 28 Nov 2026" / "Non-refundable" /
    "Cancellation fee Rs 32,017 (from 28 Nov 2026)".

    NEVER returns an empty string. Returning "" made the agent tell customers
    there was no cancellation policy at all, which is a factual error — the
    supplier always has terms, we just did not always parse them. When we truly
    cannot read them we say so and ask to confirm, which is honest.
    """
    _UNKNOWN = "Cancellation terms on request — confirm before booking"
    if option.get("has_free_cancellation"):
        base = "Free cancellation"
    else:
        base = ""
    rooms = option.get("rooms") or []
    if not isinstance(rooms, list) or not rooms:
        return base or _UNKNOWN
    first = rooms[0] if isinstance(rooms[0], dict) else {}
    terms = first.get("cancellation_policy") or []
    if not isinstance(terms, list) or not terms:
        return base or _UNKNOWN
    term = terms[0] if isinstance(terms[0], dict) else {}
    if term.get("is_nrf"):
        return "Non-refundable"
    deadline = _human_date(str(term.get("to_date") or "").strip())
    fee = term.get("cancellation_price")
    if term.get("is_free_cancellation"):
        return f"Free cancellation until {deadline}" if deadline else "Free cancellation"
    # Not free: state the fee and, when known, the date it applies from.
    if isinstance(fee, (int, float)) and fee > 0:
        from core import format_inr

        if deadline:
            return f"Cancellation fee {format_inr(round(fee))} (from {deadline})"
        return f"Cancellation fee {format_inr(round(fee))}"
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
    assume_missing: bool = False,
) -> dict[str, Any]:
    """Search hotels in the destination city. Returns options + per-night pricing.

    `assume_missing`: only True when the customer was asked for a mandatory
    field (dates, party size) and declined or told you to search anyway.

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
    from rules import missing_search_fields, needs_input_error

    # `adults` defaults to 2 in the signature, so a party size the customer
    # never gave looks identical to one they did. `rooms` carries its own count,
    # so only demand `adults` when rooms is absent.
    missing = missing_search_fields(
        check_in=check_in,
        check_out=check_out,
        **({} if rooms else {"adults": adults}),
    )
    if missing and not assume_missing:
        return needs_input_error(missing)

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

        # Flatten the cheapest room's cancellation terms, then DROP rooms[] —
        # it is 95% of the payload (10,725 → ~741 tokens per 5-hotel result)
        # and nothing renders it. Detail stays reachable via get_hotel_info.
        for o in option_dicts:
            o["cancellation_display"] = _cancellation_display(o)
            # Same tidy-up as the policy table: the supplier duplicates the
            # bed phrase and leaves a dangling "- " where the suffix was.
            if o.get("cheapest_room_type"):
                o["cheapest_room_type"] = _clean_room_name(o["cheapest_room_type"])
            # NOT `rooms` — that is the caller's party composition, and it is
            # echoed back in search_params below. Rebinding it here reported the
            # LAST hotel's room inventory as the party the customer asked for.
            hotel_rooms = o.get("rooms") or []
            o["room_options_count"] = len(hotel_rooms)
            # Refundable summary before rooms[] is dropped: the cheapest offer
            # is usually non-refundable, but most hotels DO have flexible rates.
            # per_night fields are split all-rooms vs per-room explicitly.
            try:
                _nights = int(nights or 0)
                _rooms = max(1, int(room_count or 1))
                _total = float(o.get("price_inr") or 0.0)
            except (TypeError, ValueError):
                _nights, _rooms, _total = 0, 1, 0.0
            if _nights > 0 and _total > 0:
                o["per_night_all_rooms_inr"] = round(_total / _nights, 2)
                o["per_night_per_room_inr"] = round(_total / _nights / _rooms, 2)
                # Whole stay for ONE room, so a multi-room quote can show both.
                o["total_per_room_inr"] = round(_total / _rooms, 2)
                o["rooms_booked"] = _rooms
            _summarise_refundable(o, hotel_rooms)
            # Per-room cancellation table so the customer can pick a flexible
            # rate instead of being told the hotel is "non-refundable". Capped at
            # 8 rows: enough to choose from without burying the reply.
            # CONTEXT BUDGET. room_policies was 1,912 chars per hotel — 60% of
            # the payload — and on a 5-hotel search that is ~9.5k chars of JSON
            # competing with the formatting rules for the model's attention. The
            # UI renders this table itself (_render_hotel), so the model only
            # needs a compact digest, not the full matrix.
            policies = _room_policy_breakdown(hotel_rooms, nights)
            o["room_policies"] = [
                {
                    "room": p["room"],
                    "total": p["price_display"],
                    "cancel": p["policy"],
                }
                for p in policies[:5]
            ]
            # `amenities` duplicates `amenities_display`; keep the readable one.
            o.pop("amenities", None)
            o.pop("rooms", None)

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
        amenity_note = ""
        if amenities:
            wanted = [a.strip().lower() for a in amenities if a and a.strip()]
            ids = [int(o["hotel_id"]) for o in option_dicts if str(o.get("hotel_id", "")).isdigit()]
            desc_by_id = _fetch_amenities_text(ids)
            for o in option_dicts:
                text = desc_by_id.get(str(o.get("hotel_id")), "")
                o["amenities_text"] = text
                o["amenities_matched"] = _match_amenities(wanted, text)
            # Only hotels that actually have every requested amenity. Sorting
            # them to the top was not enough: a bar-filtered search still
            # listed hotels with no bar, and the customer could not tell which
            # rows genuinely matched.
            strict = [o for o in option_dicts
                      if len(o.get("amenities_matched", [])) == len(wanted)]
            if strict:
                option_dicts = strict
            else:
                option_dicts.sort(key=lambda o: -len(o.get("amenities_matched", [])))
                amenity_note = (
                    "No hotel on these dates lists every requested amenity "
                    f"({', '.join(wanted)}). Showing the closest matches — say "
                    "which amenities each one actually has, from "
                    "`amenities_matched`, and do not claim the rest."
                )

        # Amenities / dining / a short description for every hotel on the page.
        # One batched call, best-effort — see _enrich_with_content.
        _enrich_with_content(option_dicts, city_id)

        return {
            "options": option_dicts,
            "cheapest_price_inr": options[0].price_inr if options else None,
            "nights": nights,
            "per_night_inr": options[0].per_night_inr if options else None,
            "room_count": room_count,
            "total_results": len(options),
            **({"note": note} if note else {}),
            **({"amenity_note": amenity_note} if amenity_note else {}),
            "pricing_note": (
                f"price_inr is the TOTAL for all {room_count} room(s) across the "
                f"whole {nights}-night stay — not per person and not per room. "
                f"`per_night_all_rooms_inr` is that total / {nights} nights; "
                f"`per_night_per_room_inr` divides again by {room_count} room(s); "
                f"`total_per_room_inr` is the whole stay for ONE room. "
                "The supplier publishes NO room-occupancy figure, so never "
                "state how many people a room holds as fact — the bed layout in "
                "`cheapest_room_type` is all we know. Say the room sleeps 2 "
                "'based on the bed configuration' and offer to confirm, or "
                "search the party as separate rooms and quote that. "
                + (
                    f"This party needs {room_count} rooms, so SHOW BOTH: a "
                    f"'Total ({room_count} Rooms)' column and a 'Per Room' "
                    f"column (and per-night if it fits). The customer cannot "
                    f"tell what one room costs from the combined figure alone. "
                    if room_count > 1 else ""
                ) +
                f"NEVER write an approximate price. Banned: '~', 'around', "
                f"'about', 'roughly', 'starting from about', lakh/crore "
                f"shorthand ('1.3L', '2.5 lakh'), and price RANGES "
                f"('Rs 1.3L to Rs 1.7L'). Every hotel carries "
                f"`refundable_display` with its own exact figure — name the "
                f"hotel and quote that figure verbatim, or say nothing. A "
                f"guessed '~Rs 1,17,000' overquoted a real Rs 90,508 by 25,000, "
                f"and '~Rs 1.3L to Rs 1.7L' hid exact rates of Rs 1,29,471 / "
                f"Rs 1,42,170 / Rs 1,69,946 that were already in the result. "
                f"Label whichever you show EXACTLY as its field name says — the "
                f"old note wrongly called the all-rooms figure 'per room', and a "
                f"column headed 'Per Night (Per Room)' published double the real "
                f"per-room rate. Never divide by pax."
            ),
            # search_airport_transfer_dubai needs a hotel, so it cannot run in the
            # same parallel wave as this search. The agent kept ending the turn
            # here and asking "Should I look for airport transfers?" — about
            # something the customer had already asked for. Prompt rules did not
            # hold (0/3 on the eval), so the instruction travels WITH the data
            # the next call needs.
            **(
                {
                    "next_step": {
                        "tool": "search_airport_transfer_dubai",
                        "reason": (
                            "The customer asked for airport pickup/transfers. That "
                            "search needs a hotel, which you now have — call it in "
                            "THIS turn using the recommended hotel below. Do not ask "
                            "the customer whether to look; they already asked."
                        ),
                        "hotel_name": option_dicts[0].get("hotel_name"),
                        "hotel_lat": option_dicts[0].get("latitude"),
                        "hotel_lng": option_dicts[0].get("longitude"),
                    }
                }
                if option_dicts
                else {}
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
