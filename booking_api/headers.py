"""Header builders for booking API requests.

The new staging API uses different headers per endpoint group. Always
generate a fresh X-Trace-Id for log correlation.
"""

from __future__ import annotations

import uuid

from settings import get_booking_api_settings


def _new_trace_id() -> str:
    return str(uuid.uuid4())


def base_headers() -> dict[str, str]:
    """Default headers for most non-flight endpoints."""
    s = get_booking_api_settings()
    return {
        "Authorization": f"Bearer {s.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Trace-Id": _new_trace_id(),
    }


def flight_search_headers() -> dict[str, str]:
    """Headers for /api/Flight/search."""
    s = get_booking_api_settings()
    return {
        "Authorization": f"Bearer {s.token}",
        "Content-Type": "application/json",
        "accept": "text/plain",
        "X-Requested-With": "XMLHttpRequest",
        "X-Trace-Id": _new_trace_id(),
        "X-Site-Type": "B2B",
        "X-Time-Zone": "Arabian Standard Time",
        "X-Accept-Language": "ar",
        "X-Tenant-Id": s.flight_search_tenant_id,
    }


def flight_list_headers() -> dict[str, str]:
    """Headers for /api/Flight/getflightdetails (different tenant + custom host)."""
    s = get_booking_api_settings()
    return {
        "Authorization": f"Bearer {s.token}",
        "Content-Type": "application/json",
        "accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "X-Trace-Id": _new_trace_id(),
        "X-Site-Type": "B2B",
        "X-Time-Zone": "Arabian Standard Time",
        "X-Accept-Language": "gu",
        "X-Tenant-Id": s.flight_list_tenant_id,
        "X-Custom-Host": s.flight_list_custom_host,
    }
