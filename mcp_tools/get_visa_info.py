from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_visa_info
from booking_api.endpoints import VISA_INDIAN_NATIONALITY_ID
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_visa_response
from reference_data_loader import resolve_country_id

logger = logging.getLogger(__name__)

# Spellings customers/LLMs actually use for an Indian passport.
_INDIAN_NATIONALITY_ALIASES = {
    "india",
    "indian",
    "in",
    "ind",
    "republic of india",
    "indian passport",
}


def _impl(
    destination_country: str,
    nationality_country: str,
    travel_date: str,
    adults: int = 1,
    children: int = 0,
) -> dict[str, Any]:
    """Get visa requirements and (where available) pricing."""
    try:
        country_id = resolve_country_id(destination_country)
        if country_id is None:
            return {
                "error": True,
                "message": f"Unknown destination country: {destination_country!r}",
                "error_type": "UnsupportedRoute",
            }
        # The visa service has its own country table, so we must NOT reuse
        # resolve_country_id() here (it returns 105 for India, which makes the
        # supplier return unpriced fares). Indian passport is the only
        # nationality we sell to; anything else we can't price.
        nationality = (nationality_country or "India").strip().lower()
        if nationality not in _INDIAN_NATIONALITY_ALIASES:
            return {
                "error": True,
                "message": (
                    f"Visa pricing is only available for Indian passports; "
                    f"got {nationality_country!r}."
                ),
                "error_type": "UnsupportedRoute",
            }
        raw = call_visa_info(
            country_id=country_id,
            nationality_id=VISA_INDIAN_NATIONALITY_ID,
            citizen_id=VISA_INDIAN_NATIONALITY_ID,
            travel_date=travel_date,
            adults=adults,
            children=children,
        )
        options = parse_visa_response(raw)
        option_dicts = []
        for o in options:
            d = o.model_dump()
            # Presentation-ready extras so the agent doesn't have to derive them
            # (and can't invent them): never "0 days", and a flat checklist.
            d["processing_display"] = o.processing_display
            d["price_display"] = o.price_display
            d["document_checklist"] = [doc.name for doc in o.checklist_documents]
            # Full fare matrix: Normal/Express x Adult/Child, already formatted
            # so the agent relays it rather than deriving (or inventing) it.
            d["child_price_display"] = o.child_price_display
            d["fare_lines"] = o.fare_lines
            option_dicts.append(d)
        return {
            "options": option_dicts,
            "total_results": len(options),
            "pricing_available": any(o.pricing_available for o in options),
            "pricing_note": (
                "The supplier returned no fares for these visas — quote 'On "
                "Request' and confirm with the supplier. Do NOT quote ₹0."
                if not any(o.pricing_available for o in options)
                else ""
            ),
            "search_params": {
                "destination": destination_country,
                "nationality": nationality_country,
                "travel_date": travel_date,
                "adults": adults,
                "children": children,
            },
        }
    except TripPlannerError as e:
        logger.warning("get_visa_info error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_visa_info unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl
get_visa_info_tool = tool(cache_impl("get_visa_info")(_impl))
get_visa_info_tool.name = "get_visa_info"
mcp.tool(name="get_visa_info")(_impl)
