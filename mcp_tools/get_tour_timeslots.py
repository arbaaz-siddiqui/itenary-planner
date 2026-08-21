"""get_tour_timeslots — agent + MCP tool.

Real start times for a tour on a date. Burj Khalifa returns 33 half-hourly
slots (07:00-23:00), each with a live seat count — none of which reached the
agent before, because `call_tour_timeslots` was exported and called by nothing.

Two supplier quirks this tool absorbs so the agent never sees them:

  1. **`00:00` is a placeholder, not midnight.** 25% of Dubai tours return a
     single `00:00` slot meaning "no fixed departure time". Relaying it would
     tell a customer their tour starts at midnight, so those are stripped and
     the tour is reported as not slot-based.
  2. **`supplier_id` + `option_id` are both required**, and `option_id` only
     appears in the RATES response. `search_tours` now threads both onto each
     option, so the agent can pass them straight through.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from booking_api import call_tour_timeslots
from core import TripPlannerError
from mcp_tools.server import mcp
from scheduling import usable_slots

logger = logging.getLogger(__name__)


def _impl(
    tour_id: int,
    option_id: int,
    supplier_id: int,
    travel_date: str,
    adults: int = 1,
) -> dict[str, Any]:
    """Get the bookable start times for a tour on a date.

    Args:
        tour_id:     from search_tours (`tour_id`)
        option_id:   from search_tours (`option_id`) — REQUIRED by the supplier
        supplier_id: from search_tours (`supplier_id`) — REQUIRED by the supplier
        travel_date: ISO yyyy-mm-dd
        adults:      party size, for availability

    Returns:
        {slots: [{time, available, slot_id}], is_slot_based, total_slots,
         first_slot, last_slot}

        `is_slot_based=False` means the tour has NO fixed departure times — quote
        it as flexible, do NOT say "no availability".
    """
    try:
        raw = call_tour_timeslots(
            tour_id=tour_id,
            tour_option_id=str(option_id),
            travel_date=travel_date,
            supplier_id=supplier_id,
            adults=adults,
        )
        rows = raw.get("result") or raw.get("data") or []
        if not isinstance(rows, list):
            rows = []

        real = usable_slots(rows)
        slots = [
            {
                "time": f"{t.hour:02d}:{t.minute:02d}",
                "available": row.get("available"),
                "slot_id": str(row.get("timeSlotId") or ""),
            }
            for t, row in real
        ]
        return {
            "slots": slots,
            "is_slot_based": bool(slots),
            "total_slots": len(slots),
            "first_slot": slots[0]["time"] if slots else "",
            "last_slot": slots[-1]["time"] if slots else "",
            "note": (
                ""
                if slots
                else (
                    "This tour has no fixed departure times — it is flexible "
                    "through the day. Do NOT tell the customer it is unavailable."
                )
            ),
            "search_params": {
                "tour_id": tour_id,
                "option_id": option_id,
                "supplier_id": supplier_id,
                "travel_date": travel_date,
                "adults": adults,
            },
        }
    except TripPlannerError as e:
        logger.warning("get_tour_timeslots error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_tour_timeslots unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


from mcp_tools.result_cache import cache_impl  # noqa: E402

get_tour_timeslots_tool = tool(cache_impl("get_tour_timeslots")(_impl))
get_tour_timeslots_tool.name = "get_tour_timeslots"
mcp.tool(name="get_tour_timeslots")(_impl)
