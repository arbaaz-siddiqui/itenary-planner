"""lookup_entity — public autocomplete search for hotels, tours, restaurants, airlines.

No auth token required. Used by the agent to resolve a user-typed name
("Atlantis", "Desert Safari", "Khandani Rajdhani") to the numeric ID that all
booking endpoints expect. The `location_id` returned here IS the hotel_id /
tour_id / restaurant_id used everywhere else in the system.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_entity_search
from mcp_tools.server import mcp
from text_match import match_fraction, matches, terms, without_words

logger = logging.getLogger(__name__)

# "hotels" deliberately excluded: this endpoint is worldwide, so "Howard
# Johnson" resolved to US/China properties. Hotels go via city-scoped
# search_hotels(hotel_name=...) instead.
_VALID_SERVICES = {"tours", "restaurants", "airlines"}
_REDIRECTED_SERVICES = {
    # Deliberately free of "not found" / "does not exist" wording: a model
    # skimming this could echo the phrase to the customer, which is the exact
    # failure being prevented. This is a ROUTING instruction, not a result.
    "hotels": (
        "Wrong tool for hotels — this search is worldwide and would return "
        "same-brand properties in other countries. Retry with "
        "search_hotels(destination_city=<city>, hotel_name=<name as the customer "
        "said it>): it is city-scoped, resolves the property against bookable "
        "inventory, and returns live availability and pricing in one step. "
        "This message says nothing about whether the property is available — "
        "only search_hotels can tell you that."
    ),
}


# What to do with the id this lookup returns. The hint used to be built as
# f"search_{svc[:-1]}" — "search_tour", "search_restaurant" — tools that do not
# exist, so the model hit a dead end and answered from memory instead of
# fetching. Asked about desert safari add-ons it reported "no add-ons listed"
# while the supplier had a drinks package and two Majlis options.
_NEXT_STEP: dict[str, str] = {
    "tours": (
        "Call get_tour_options(tour_id=best_match_id, travel_date=...) NOW for "
        "this tour's bookable variants and add-ons (drinks packages, VIP Majlis, "
        "upgrades), each with its own price. `best_match_id` IS the tour they "
        "meant — the other rows are near-duplicates, so do not weigh them up "
        "and never reply without calling the tool. Use get_tour_details only "
        "for prose, and search_tours(query=...) to list other tours. NEVER say "
        "a tour has no add-ons without calling get_tour_options first."
    ),
    "restaurants": (
        "Call get_restaurant_details(restaurant_id=best_match_id) NOW for the "
        "rating, per-meal timings and dishes. `best_match_id` is the one they "
        "meant."
    ),
    "airlines": "Use best_match_name as airline_filter in search_flights.",
}
_DEFAULT_NEXT_STEP = (
    "Use best_match_id / best_match_name in the matching search or detail "
    "tool — do not weigh up the other rows, and do not answer from memory."
)

# Services whose rows only mean something when their NAME matches the query.
# For restaurants the supplier returns the city's whole list when nothing
# matches: "Armani Ristorante" came back as Rangoli, Atithi, Bombay Bites...,
# and naming the first of those "the one they meant" sent the agent to fetch
# Rangoli's details for a question about Armani. Tours are left out on
# purpose: their search matches on content ("dune bashing" -> a desert
# safari), so the top row is meaningful even without a name match.
_NAME_MATCH_REQUIRED = {"restaurants"}

# What to do when nothing we sell matches. Never the hotel wording ("do not
# tell the customer the hotel doesn't exist") -- for a restaurant we do not
# sell, saying so plainly is the correct answer.
_NOT_FOUND_STEP: dict[str, str] = {
    "restaurants": (
        "We do not sell a restaurant matching {query!r}{where}. Tell the "
        "customer that plainly, in one sentence. Do NOT describe it — not its "
        "cuisine, style (fine dining / café), price, rating or location — even "
        "if you have heard of it: we hold no data on it, and describing it from "
        "general knowledge is how wrong locations were invented. Then offer "
        "what we do sell: search_restaurants(destination_city=<city>) lists it. "
        "Do not promise a kind of restaurant ('other fine dining options') — "
        "offer to show what we have."
    ),
}
_DEFAULT_NOT_FOUND_STEP = (
    "Nothing named {query!r}{where} was found. Do not describe it from general "
    "knowledge; use the matching search tool for the destination instead."
)


def _impl(
    service: str,
    query: str,
    size: int = 5,
    city: str = "",
) -> dict[str, Any]:
    """Resolve a TOUR, RESTAURANT or AIRLINE name to its numeric ID.

    NOT FOR HOTELS. For any named hotel use
    `search_hotels(destination_city=..., hotel_name=...)` — it is city-scoped
    and returns live availability and pricing in one call. This tool searches
    worldwide and will happily return a same-brand hotel on another continent.

    Use before booking/detail calls when the user names a specific tour,
    restaurant or airline — e.g. "Desert Safari", "Khandani Rajdhani",
    "Emirates". Returns the `location_id` accepted by the other tools.

    Args:
        service:  "tours", "restaurants", or "airlines" (NOT "hotels")
        query:    Free-text name typed by the user
        size:     Max results to return (default 5, max 20)
        city:     Destination city to scope to, e.g. "Dubai". ALWAYS pass this
                  when you know the destination — results are worldwide
                  otherwise. In-city matches rank first and `scoped_note` warns
                  when nothing matches.

    Returns a list of matches, each with:
        - id (int)              ← use this as hotel_id / tour_id / restaurant_id
        - name (str)            ← exact supplier name
        - type (str)            ← "hotel", "city", "Tour", "Restaurant", "Airline"
        - city (str)
        - country (str)
        - city_id (int)         ← cityId for that entity
        - country_id (int)      ← countryId
        - latitude / longitude  ← location (0,0 if unknown)
        - iata_code (str)       ← airlines only, empty string for others
        - total_services (int)  ← how many bookable services this entity has

    Usage examples (note: every one passes `city`):
        lookup_entity(service="tours", query="desert safari", city="Dubai")
          → [{id: 244, name: "Desert Safari In Faqa", type: "Tour", city: "Dubai", ...}]

        lookup_entity(service="restaurants", query="khandani rajdhani", city="Dubai")
          → [{id: 4, name: "Khandani Rajdhani - Indian Restaurant in Karama", ...}]

        lookup_entity(service="airlines", query="emirates")
          → [{id: 176, name: "Emirates", iata_code: "EK", ...}]

        # For a hotel, do NOT use this tool:
        search_hotels(destination_city="Dubai", hotel_name="Howard Johnson", ...)
    """
    svc = service.lower().strip()
    # Recoverable redirect: tell the model exactly which tool to call instead,
    # rather than a bare "invalid service" it might report to the customer.
    if svc in _REDIRECTED_SERVICES:
        return {
            "error": True,
            "error_type": "WrongTool",
            "message": _REDIRECTED_SERVICES[svc],
            "use_instead": "search_hotels",
            "retry_with": {
                "tool": "search_hotels",
                "destination_city": city or "<the trip destination>",
                "hotel_name": query.strip(),
            },
        }
    if svc not in _VALID_SERVICES:
        return {
            "error": True,
            "message": f"Invalid service '{service}'. Must be one of: {', '.join(sorted(_VALID_SERVICES))}",
        }
    if not query or not query.strip():
        return {"error": True, "message": "query cannot be empty"}

    size = min(max(1, size), 20)

    try:
        raw = call_entity_search(service=svc, query=query.strip(), size=size)
    except Exception as e:
        logger.warning("lookup_entity call failed: %s", e)
        return {"error": True, "message": str(e), "results": []}

    items = raw.get("data") or []
    if not isinstance(items, list):
        return {"error": True, "message": "Unexpected response format", "results": []}

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        loc_id = item.get("location_id", 0)
        results.append({
            "id": int(loc_id) if loc_id else 0,
            "name": item.get("name_text", ""),
            "type": item.get("type", ""),
            "city": item.get("city", ""),
            "country": item.get("country", ""),
            "city_id": item.get("cityId", 0),
            "country_id": item.get("countryId", 0),
            "latitude": item.get("latitude", 0),
            "longitude": item.get("longitude", 0),
            "iata_code": item.get("iataCode", ""),
            "total_services": item.get("totalAvailableServices", 0),
        })

    # Filter out city/region rows (id==0 or type not matching service) when
    # caller searches hotels — city rows don't have a bookable hotel_id.
    hotel_results = [r for r in results if r["id"] > 0 and r["type"].lower() not in ("city", "high_level_region", "neighborhood")]

    # City scoping. This endpoint searches GLOBALLY: "Howard Johnson" returns
    # Bakersfield / Changsha / Yibin and NOT the Dubai property, which is how a
    # customer asking about a Dubai hotel was told it only exists in the US and
    # China. When the caller knows the destination, keep in-city matches first
    # and report how many were dropped so the agent can't silently read out a
    # foreign list.
    city_filter = (city or "").strip().lower()
    out_of_city = 0
    if city_filter and hotel_results:
        in_city = [r for r in hotel_results if city_filter in str(r.get("city", "")).lower()]
        out_of_city = len(hotel_results) - len(in_city)
        if in_city:
            # Rank in-city first; keep the rest so the agent can still see them.
            hotel_results = in_city + [r for r in hotel_results if r not in in_city]

    # Name relevance. The supplier orders results its own way, so for "burj
    # khalifa" results[0] was "Sky Views Edge Walk" while two genuine Burj
    # entries sat below it. Every hint says to use results[0], and faced with
    # three near-identical candidates and no signal which was meant, the model
    # stalled and returned an EMPTY reply to the customer. Rank the rows that
    # actually contain the query words first.
    if terms(query) and hotel_results:
        def _relevance(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
            i, row = pair
            # Any query word present counts as relevant here (ranking only);
            # spelling variants count too ("Kathiyawadi" / "Kathhiyawadi").
            share = match_fraction(query, str(row.get("name", "")))
            # City first: sorting on name alone put Abu Dhabi desert safaris
            # above Dubai ones, undoing the scoping directly above.
            off_city = (
                0 if not city_filter
                else (0 if city_filter in str(row.get("city", "")).lower() else 1)
            )
            # Then how much of the query the name carries: "Dubai Desert
            # Safari by Gujjutours" must beat "Dubai Desert Safari" when the
            # customer named the former. Ties keep the supplier's order; a
            # tie between a sellable and an empty listing (both Burj rows
            # carry every query word) is settled by _first_bookable_tour.
            return (off_city, -round(share, 2), i)

        hotel_results = [
            row for _i, row in sorted(enumerate(hotel_results), key=_relevance)
        ]

    # The resolved target, stated once. Handing over five rows and a hint to
    # "pass results[0]['id']" is not a decision: asked for Burj Khalifa the
    # supplier returns three near-identical Burj rows, and the model deliberated
    # instead of acting, emitting an EMPTY message to the customer (5 empty
    # turns in 5 runs; 0 when the same call returned a single row). Naming the
    # top-ranked row removes the choice.
    in_scope = [
        r for r in hotel_results
        if not city_filter or city_filter in str(r.get("city", "")).lower()
    ]
    if svc in _NAME_MATCH_REQUIRED:
        identity = without_words(query, city) if city else query
        best = next((r for r in in_scope if _names_query(r, identity, in_scope)), None)
    elif svc == "tours":
        best = _first_bookable_tour(in_scope or hotel_results)
        # The bookable tour leads the list too, so results[0] and best_match
        # never disagree.
        if best is not None:
            hotel_results = [best] + [r for r in hotel_results if r is not best]
    else:
        best = hotel_results[0] if hotel_results else None

    where = f" in {city}" if city else ""
    not_found = (
        svc in _NAME_MATCH_REQUIRED and best is None
    ) or (bool(city_filter) and not in_scope)

    scoped_note = ""
    if not_found:
        scoped_note = _NOT_FOUND_STEP.get(svc, _DEFAULT_NOT_FOUND_STEP).format(
            query=query.strip(), where=where
        )
        if hotel_results:
            scoped_note += " The rows in `results` are other entries, not the one asked about."
    elif city_filter and out_of_city:
        scoped_note = (
            f"Ranked {len(hotel_results) - out_of_city} match(es) in {city} first; "
            f"{out_of_city} result(s) are in other cities — do NOT offer those."
        )

    return {
        "results": hotel_results,
        "total": len(hotel_results),
        # Ready to pass straight into the next tool — no picking required.
        "best_match": best,
        "best_match_id": (best or {}).get("id"),
        "best_match_name": (best or {}).get("name"),
        "service": svc,
        "query": query.strip(),
        "city_filter": city or "",
        "scoped_note": scoped_note,
        "usage_hint": (
            "This search is GLOBAL — always check each result's 'city' before showing it. "
            "For a named hotel in a known destination, prefer "
            "search_hotels(destination_city=..., hotel_name=...) which is city-scoped. "
            "Otherwise: pass best_match_id as hotel_ids=[id] in get_hotel_info, "
            "or best_match_name as hotel_name in search_hotels. "
            "Also capture best_match['latitude'] and best_match['longitude'] — "
            "you will need these as hotel_lat/hotel_lng for search_airport_transfer_dubai."
            if svc == "hotels" else
            scoped_note if not_found else
            _NEXT_STEP.get(svc, _DEFAULT_NEXT_STEP)
        ),
    }


# How many top-ranked tours to check for bookable options. The supplier's own
# order puts the real product within the first few rows.
_BOOKABLE_CHECK = 5


def _first_bookable_tour(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The highest-ranked tour that actually has bookable options.

    The autocomplete cannot tell a sellable tour from an empty listing
    ("Burj Khalifa Tickets" 28482 and "At The Top, Burj Khalifa" 28488 both
    read totalAvailableServices 2), and its order drifts: when 28482 came
    first, get_tour_options returned nothing, the agent looped through
    detail calls and the customer saw a raw recursion error. One options call
    per candidate, in parallel, settles it. Falls back to the top row if the
    check itself fails, so a supplier hiccup never empties the answer.
    """
    import concurrent.futures as cf
    from datetime import timedelta

    from booking_api.endpoints import call_tour_options
    from core import supplier_today

    candidates = [r for r in rows if r.get("id")][:_BOOKABLE_CHECK]
    if not candidates:
        return None
    probe_date = (supplier_today() + timedelta(days=7)).isoformat()

    def bookable(row: dict[str, Any]) -> bool | None:
        try:
            listed = call_tour_options(tour_id=int(row["id"]), travel_date=probe_date)
        except Exception:  # noqa: BLE001 -- unknown, not "empty"
            return None
        return bool(((listed or {}).get("result") or {}).get("tourOptionlist"))

    with cf.ThreadPoolExecutor(max_workers=len(candidates)) as ex:
        verdicts = list(ex.map(bookable, candidates))
    return next(
        (row for row, ok in zip(candidates, verdicts, strict=True) if ok),
        next((row for row, ok in zip(candidates, verdicts, strict=True) if ok is None),
             candidates[0]),
    )


def _names_query(row: dict[str, Any], identity: str, rows: list[dict[str, Any]]) -> bool:
    """Is `row` the entity the customer named, not just a row that came back?

    At least half the query's words must appear in the name, and at least one
    of those must be DISTINCTIVE -- found in fewer than half of the returned
    names. "Veganesha restaurant" names Veganesha: "restaurant" is in most
    rows and proves nothing, "veganesha" is in one. "Armani Ristorante" names
    none of the five restaurants returned for it. With a single row there is
    nothing to compare against, so any matching word counts.
    """
    name = str(row.get("name", ""))
    q = terms(identity)
    if not q or match_fraction(identity, name) < 0.5:
        return False
    names = [str(r.get("name", "")) for r in rows]

    def distinctive(term: str) -> bool:
        if len(names) < 2:
            return True
        return sum(1 for n in names if matches(term, n)) * 2 < len(names)

    return any(matches(t, name) and distinctive(t) for t in q)


from mcp_tools.result_cache import cache_impl

lookup_entity_tool = tool(cache_impl("lookup_entity")(_impl))
lookup_entity_tool.name = "lookup_entity"
mcp.tool(name="lookup_entity")(_impl)
