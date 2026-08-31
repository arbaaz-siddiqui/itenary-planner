"""search_transfers — agent + MCP tool.

Defaults to the airport→hotel pattern for Dubai. The agent passes hotel
coordinates; we use the city's default airport from reference_data.

Shared vs Private pricing logic (from client spec):
  - Shared: price is PER PERSON (vehicle cost ÷ pax count)
  - Private: price is PER VEHICLE regardless of how many people ride
  The tool returns both types but adds per_person_inr for shared options
  so the agent can present the correct cost framing to the customer.

Transfer type filter:
  Pass transfer_type="Shared" or transfer_type="Private" to return only
  that type. Omit to return all options sorted cheapest first.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_transfer_search
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_transfer_response
from reference_data_loader import get_default_dubai_airport

logger = logging.getLogger(__name__)


def _hotel_name_from_coords(lat: float, lng: float) -> str | None:
    """Reverse-resolve the EXACT hotel name at (lat, lng) via the public
    autocomplete API, which covers ALL hotels (not just the ~60 discovered ones).

    The transfer supplier matches inventory on toLocationName, so when the LLM
    passes coordinates without a name we must recover the RIGHT hotel — not just
    some nearby one. The autocomplete index returns lat/lng per hotel; we query a
    broad set and pick the closest coordinate match within a tight radius. Only
    returns a name when the match is essentially exact (same building), else None
    so we don't send a wrong hotel's name.
    """
    try:
        from booking_api import call_entity_search

        # A broad, generic query returns many Dubai hotels with their coords.
        # We then pick the one whose coordinates match the target almost exactly.
        candidates: list[dict] = []
        for q in ("hotel dubai", "dubai"):
            res = call_entity_search(service="hotels", query=q, size=40)
            for h in res.get("data") or []:
                if (
                    isinstance(h, dict)
                    and h.get("type", "").lower() == "hotel"
                    and h.get("latitude") and h.get("longitude")
                ):
                    candidates.append(h)
        best, best_d2 = None, None
        for h in candidates:
            d2 = (float(h["latitude"]) - lat) ** 2 + (float(h["longitude"]) - lng) ** 2
            if best_d2 is None or d2 < best_d2:
                best_d2, best = d2, h
        # 0.000001 deg² ≈ ~1m at Dubai's latitude — require a near-exact match so
        # we never substitute a different hotel.
        if best is not None and best_d2 is not None and best_d2 < 1e-6:
            return best.get("name_text") or None
    except Exception as e:  # noqa: BLE001
        logger.warning("coord->hotel-name resolve failed: %s", e)
    return None


def _impl(
    arrival_date: str,
    hotel_name: str = "",
    hotel_lat: float | None = None,
    hotel_lng: float | None = None,
    arrival_time: str = "12:00",
    return_date: str | None = None,
    return_time: str = "12:00",
    adults: int = 2,
    transfer_type: str = "",
    hotel_place_id: str = "",
    max_results: int = 20,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search Dubai airport ↔ hotel transfers with shared/private pricing logic.

    Args:
        arrival_date:   Arrival date ISO (yyyy-mm-dd) — REQUIRED.
        hotel_name:     The hotel the customer picked (from search_hotels). STRONGLY
                        preferred — with a good name the tool resolves coords itself.
        hotel_lat:      Hotel latitude (optional) — the `latitude` from search_hotels.
        hotel_lng:      Hotel longitude (optional) — the `longitude` from search_hotels.
                        Pass hotel_name OR coords; name alone is enough.
        arrival_time:   Arrival time HH:MM (default "12:00")
        return_date:    Departure date for return leg ISO (yyyy-mm-dd), or None for one-way
        return_time:    Return pickup time HH:MM (default "12:00")
        adults:         Total number of passengers — IMPORTANT for per-person cost calculation
        transfer_type:  Filter results — "Shared", "Private", or "" for all types.
                        Leave "" and DO NOT ask the customer first. Dubai airport
                        inventory is currently Private-only, so asking "shared or
                        private?" wastes a turn and then disappoints. Search, then
                        state what actually came back.
        hotel_place_id: Google Place ID of the hotel. Optional — leave empty string if unknown.
                        The transfer API can match on lat/lng coordinates alone.
        max_results:    Max options to return (default 20)

    PRICING RULES (always explain these to the customer):
        Shared transfer  → price_inr is the TOTAL vehicle cost.
                           per_person_inr = price_inr ÷ adults is what EACH person pays.
                           E.g. vehicle costs ₹1,500 for 6 people → ₹250/person.
        Private transfer → price_inr is the TOTAL for the whole vehicle.
                           All passengers ride together, no per-person split needed.
                           E.g. vehicle costs ₹3,000 for up to 6 people → ₹3,000 total.

    WHEN TO CALL:
        As soon as you have a hotel — do NOT wait for the customer to pick one and
        do NOT ask permission first. If they mentioned pickup/transfers at all,
        they already asked. Take the recommended hotel's `hotel_name` from the
        search_hotels result, call this in the SAME turn, and say which hotel the
        transfers are for. Re-run if they later choose a different hotel.
    """
    try:
        airport = get_default_dubai_airport()
        if not airport:
            return {
                "error": True,
                "message": "Dubai airport coordinates not configured",
                "error_type": "MissingReferenceData",
            }

        # The supplier matches on the destination NAME ("Hotel" → 2 rows, real
        # name → 60+), so resolve a real hotel name ourselves: canonicalise a
        # given name via entity search, else reverse-resolve from coords.
        _generic = (not hotel_name) or hotel_name.strip().lower() in ("", "hotel", "the hotel")
        try:
            from booking_api import call_entity_search
            if not _generic:
                # Name given → entity search canonicalises it AND gives authoritative
                # coords. This is why coords are OPTIONAL: a good name is enough.
                # City-scope the query FIRST. Entity search is worldwide, so a
                # generic name ("Social Hotel") matches Portugal/Jaipur/Bogota
                # long before the Dubai property. Try "<name> Dubai", then fall
                # back to the bare name.
                _q = hotel_name.strip()
                _queries = [_q] if "dubai" in _q.lower() else [f"{_q} Dubai", _q]
                hits: list[dict] = []
                for _query in _queries:
                    res = call_entity_search(service="hotels", query=_query, size=5)
                    hits = [
                        h for h in (res.get("data") or [])
                        if isinstance(h, dict) and h.get("type", "").lower() == "hotel"
                        and h.get("latitude") and h.get("longitude")
                    ]
                    if hits:
                        break
                if hits:
                    hotel_lat = float(hits[0]["latitude"])
                    hotel_lng = float(hits[0]["longitude"])
                    hotel_name = hits[0].get("name_text") or hotel_name
                    logger.info("transfer_resolved_by_name name=%s lat=%s lng=%s",
                                hotel_name, hotel_lat, hotel_lng)
            elif hotel_lat is not None and hotel_lng is not None:
                # No usable name but we have coords — reverse-resolve the nearest
                # hotel so we can send a real toLocationName the supplier matches on.
                resolved = _hotel_name_from_coords(hotel_lat, hotel_lng)
                if resolved:
                    hotel_name = resolved
                    logger.info("transfer_resolved_by_coords name=%s lat=%s lng=%s",
                                hotel_name, hotel_lat, hotel_lng)
        except Exception as _e:
            logger.warning("transfer hotel resolve failed: %s", _e)

        # Neither a usable name NOR coordinates → we can't search. Ask the agent to
        # get the hotel first (graceful — no crash, no fake "unavailable").
        if hotel_lat is None or hotel_lng is None:
            return {
                "options": [],
                "total_results": 0,
                "available": False,
                "message": "Need the customer's hotel first to search airport transfers.",
                "agent_instructions": (
                    "You called transfers without a hotel. First confirm which hotel "
                    "the customer picked (from search_hotels), then call this tool "
                    "again with that hotel_name. Do NOT tell the customer transfers "
                    "are unavailable — you simply need the hotel."
                ),
            }

        raw = call_transfer_search(
            from_lat=float(airport["lat"]),
            from_lng=float(airport["lng"]),
            to_lat=hotel_lat,
            to_lng=hotel_lng,
            from_place_id=str(airport.get("place_id", "ChIJaQ4mkwZdXz4R6e5IegDUleY")),
            to_place_id=hotel_place_id or "",
            from_location_name=str(airport.get("name", "Dubai International Airport(dxb)")),
            to_location_name=hotel_name or "Hotel",
            departure_date=arrival_date,
            departure_time=arrival_time,
            return_date=return_date,
            return_time=return_time,
            is_round_trip=return_date is not None,
            from_type="A",
            to_type="P",  # P = Property/hotel destination (client cURL)
            adults=adults,
        )

        all_options = parse_transfer_response(raw, max_results=None)

        # Filter by transfer_type if specified
        filter_key = transfer_type.strip().lower()
        if filter_key in ("shared", "private"):
            filtered = [o for o in all_options if o.transfer_type.lower() == filter_key]
        else:
            filtered = all_options

        # Apply per-person pricing for shared options
        pax = max(adults, 1)
        options_out = []
        for o in filtered[:max_results]:
            d = o.model_dump()
            if o.transfer_type.lower() == "shared":
                # Shared: vehicle has a fixed cost — divide by passenger count
                d["per_person_inr"] = round(o.price_inr / pax)
                d["pricing_note"] = (
                    f"Shared vehicle — total ₹{o.price_inr:,.0f} ÷ {pax} people "
                    f"= ₹{d['per_person_inr']:,.0f} per person"
                )
            else:
                # Private: price is for the whole vehicle regardless of pax count
                d["per_person_inr"] = None
                d["pricing_note"] = (
                    f"Private vehicle — ₹{o.price_inr:,.0f} total for up to "
                    f"{o.capacity or pax} people (not per person)"
                )
            options_out.append(d)

        if not options_out:
            return {
                "options": [],
                "total_results": 0,
                "available": False,
                "message": (
                    "No airport transfers are available from our supplier for this "
                    "hotel/date. Tell the customer plainly that we don't have a "
                    "transfer for this route right now."
                ),
                "agent_instructions": (
                    "Say we have no transfer available for these dates and offer to "
                    "try different dates. DO NOT invent Careem/Uber/private car "
                    "services or any provider we didn't search — we only sell what "
                    "this tool returns. DO NOT ask the customer more questions about "
                    "pax or vehicle type; the search already ran and found nothing."
                ),
            }

        return {
            "options": options_out,
            "cheapest_price_inr": options_out[0]["price_inr"] if options_out else None,
            "cheapest_per_person_inr": options_out[0].get("per_person_inr") if options_out else None,
            "total_results": len(options_out),
            "transfer_type_filter": transfer_type or "all",
            "adults": adults,
            "pricing_rule": (
                "Shared = per-person cost (price_inr ÷ adults). "
                "Private = per-vehicle cost (fixed regardless of pax count)."
            ),
            # The agent offered customers a "Private car or Shared shuttle?"
            # choice on a route where the supplier returns Private only. State
            # what actually came back so it cannot offer phantom inventory.
            "types_available": sorted({o["transfer_type"] for o in options_out}),
            "availability_note": (
                (
                    "Only PRIVATE vehicles are available for this route — do NOT "
                    "offer the customer a shared option or ask them to choose "
                    "between shared and private."
                )
                if options_out and {o["transfer_type"] for o in options_out} == {"Private"}
                else ""
            ),
            "search_params": {
                "hotel_lat": hotel_lat,
                "hotel_lng": hotel_lng,
                "arrival_date": arrival_date,
                "return_date": return_date,
                "adults": adults,
                "transfer_type_filter": transfer_type or "all",
            },
        }

    except TripPlannerError as e:
        logger.warning("search_transfers error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_transfers unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl

search_transfers_tool = tool(cache_impl("search_airport_transfer_dubai")(_impl))
search_transfers_tool.name = "search_airport_transfer_dubai"
mcp.tool(name="search_airport_transfer_dubai")(_impl)
