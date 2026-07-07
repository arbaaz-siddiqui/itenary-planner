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


def _nearest_hotel_name(lat: float, lng: float, city_id: int = 244520) -> str | None:
    """Reverse-resolve the real hotel NAME nearest to (lat, lng).

    The transfer supplier matches inventory on toLocationName, so when the LLM
    passes coordinates without a usable name we look up the closest hotel in the
    city's static data (address endpoint has lat/long per hotel_id; the optimize
    endpoint has names) and return that hotel's real name. Best-effort → None on
    any failure, so the caller falls back to whatever name it had.
    """
    try:
        from booking_api import (
            call_hotel_property_info,
            call_hotel_static_data,
            discover_city_hotel_ids,
        )
        from parsers import parse_hotel_static_data_response

        ids = [hid for hid, _ in discover_city_hotel_ids(city_id)[:60]]
        if not ids:
            return None
        addr = parse_hotel_static_data_response(call_hotel_property_info(hotel_ids=ids))
        best_id, best_d2 = None, None
        for h in addr:
            hlat, hlng = h.get("latitude"), h.get("longitude")
            if hlat is None or hlng is None:
                continue
            d2 = (float(hlat) - lat) ** 2 + (float(hlng) - lng) ** 2
            if best_d2 is None or d2 < best_d2:
                best_d2, best_id = d2, h.get("hotel_id")
        # ~0.0009 deg² ≈ 3km; only trust a genuinely close match
        if best_id is None or (best_d2 is not None and best_d2 > 0.0009):
            return None
        names = parse_hotel_static_data_response(call_hotel_static_data(hotel_ids=[best_id]))
        for n in names:
            if n.get("hotel_id") == best_id and n.get("hotel_name"):
                nm = n["hotel_name"]
                return nm if not nm.startswith("Hotel ") else None
    except Exception as e:  # noqa: BLE001
        logger.warning("nearest-hotel resolve failed: %s", e)
    return None


def _impl(
    hotel_lat: float,
    hotel_lng: float,
    arrival_date: str,
    arrival_time: str = "12:00",
    return_date: str | None = None,
    return_time: str = "12:00",
    adults: int = 2,
    transfer_type: str = "",
    hotel_place_id: str = "",
    hotel_name: str = "",
    max_results: int = 20,
) -> dict[str, Any]:
    """Search Dubai airport ↔ hotel transfers with shared/private pricing logic.

    Args:
        hotel_lat:      Hotel latitude — use the `latitude` field from the hotel in search_hotels result.
                        Each hotel in search_hotels now includes latitude/longitude directly.
        hotel_lng:      Hotel longitude — use the `longitude` field from the hotel in search_hotels result.
        arrival_date:   Arrival date ISO (yyyy-mm-dd)
        arrival_time:   Arrival time HH:MM (default "12:00")
        return_date:    Departure date for return leg ISO (yyyy-mm-dd), or None for one-way
        return_time:    Return pickup time HH:MM (default "12:00")
        adults:         Total number of passengers — IMPORTANT for per-person cost calculation
        transfer_type:  Filter results — "Shared", "Private", or "" for all types.
                        Ask the customer BEFORE calling: "Private gaadi chahiye ya sharing?"
        hotel_place_id: Google Place ID of the hotel. Optional — leave empty string if unknown.
                        The transfer API can match on lat/lng coordinates alone.
        hotel_name:     Hotel name for display purposes.
        max_results:    Max options to return (default 5)

    PRICING RULES (always explain these to the customer):
        Shared transfer  → price_inr is the TOTAL vehicle cost.
                           per_person_inr = price_inr ÷ adults is what EACH person pays.
                           E.g. vehicle costs ₹1,500 for 6 people → ₹250/person.
        Private transfer → price_inr is the TOTAL for the whole vehicle.
                           All passengers ride together, no per-person split needed.
                           E.g. vehicle costs ₹3,000 for up to 6 people → ₹3,000 total.

    WHEN TO CALL:
        After confirming flight + hotel. Ask: "Airport transfer chahiye?"
        If yes, ask: "Private chahiye ya shared (sharing mein sasta padta hai)?"
        Then call with the appropriate transfer_type filter and the actual adults count.
    """
    try:
        airport = get_default_dubai_airport()
        if not airport:
            return {
                "error": True,
                "message": "Dubai airport coordinates not configured",
                "error_type": "MissingReferenceData",
            }

        # CRITICAL: the supplier matches transfer inventory on the DESTINATION
        # NAME, not just coordinates. Empirically, toLocationName="Hotel" (the
        # default when the LLM omits hotel_name) returns ~2 rows; the REAL hotel
        # name returns 60+. So a usable, specific hotel_name is mandatory.
        #
        # The LLM is unreliable here — it often passes correct coords but NO name
        # (or a generic "Hotel"). So we resolve a real name ourselves:
        #   (a) if a specific name was given, use entity search to canonicalise it
        #       and get authoritative coords;
        #   (b) if the name is missing/generic, reverse-resolve the nearest hotel
        #       from the coordinates via the city address data.
        _generic = (not hotel_name) or hotel_name.strip().lower() in ("", "hotel", "the hotel")
        try:
            from booking_api import call_entity_search
            if not _generic:
                res = call_entity_search(service="hotels", query=hotel_name.strip(), size=5)
                hits = [
                    h for h in (res.get("data") or [])
                    if isinstance(h, dict) and h.get("type", "").lower() == "hotel"
                    and h.get("latitude") and h.get("longitude")
                ]
                if hits:
                    hotel_lat = float(hits[0]["latitude"])
                    hotel_lng = float(hits[0]["longitude"])
                    hotel_name = hits[0].get("name_text") or hotel_name
                    logger.info("transfer_resolved_by_name name=%s lat=%s lng=%s",
                                hotel_name, hotel_lat, hotel_lng)
            else:
                # No usable name — find the nearest hotel to the given coords so we
                # can send a real toLocationName the supplier will match on.
                resolved = _nearest_hotel_name(hotel_lat, hotel_lng)
                if resolved:
                    hotel_name = resolved
                    logger.info("transfer_resolved_by_coords name=%s lat=%s lng=%s",
                                hotel_name, hotel_lat, hotel_lng)
        except Exception as _e:
            logger.warning("transfer hotel resolve failed: %s", _e)

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


search_transfers_tool = tool(_impl)
search_transfers_tool.name = "search_airport_transfer_dubai"
mcp.tool(name="search_airport_transfer_dubai")(_impl)
