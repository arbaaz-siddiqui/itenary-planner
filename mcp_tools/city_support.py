"""One wording for "we cannot search that city", shared by every search tool.

The supplier sells worldwide, so a city is refused for one of two honest
reasons only: the name is not a city we (or the supplier) can find, or the
supplier has nothing of this kind there. Both keep error_type
"UnsupportedRoute" so callers that branch on it keep working.
"""

from __future__ import annotations

from typing import Any

# A bare "Unsupported destination" got paraphrased to the customer as "I don't
# have that in my database", which reads as OUR system being broken.
_NEVER_SAY = (
    "Never say the data is missing, unavailable, or not in your database, and "
    "never imply a technical problem."
)


def unknown_city(destination_city: str, service: str) -> dict[str, Any]:
    """The name did not resolve to any city the supplier knows."""
    return {
        "error": True,
        "error_type": "UnsupportedRoute",
        "message": f"We could not find a city called {destination_city!r} for {service}.",
        "agent_instructions": (
            f"Tell the customer we could not find {destination_city!r} as a city "
            f"and ask which city they mean (a spelling or the nearest big city "
            f"helps). {_NEVER_SAY}"
        ),
    }


def nothing_in_city(city_name: str, service: str) -> dict[str, Any]:
    """The city is real but the supplier sells no `service` there."""
    return {
        "error": True,
        "error_type": "UnsupportedRoute",
        "message": f"The supplier has no {service} in {city_name}.",
        "agent_instructions": (
            f"Say plainly that we do not offer {service} in {city_name} right now. "
            f"{_NEVER_SAY} Offer the other services for this trip or a nearby "
            f"city, and do not ask the customer for more details first: the city "
            f"is the blocker, not their input."
        ),
    }


def require_city(destination_city: str, service: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(city record, None) when the city has a supplier id, else (None, error)."""
    from reference_data_loader import resolve_city

    city = resolve_city(destination_city)
    if city is None:
        return None, unknown_city(destination_city, service)
    if not city.get("city_id") or not city.get("country_id"):
        return None, nothing_in_city(str(city.get("name") or destination_city), service)
    return city, None
