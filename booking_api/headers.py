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


def hotel_static_headers() -> dict[str, str]:
    """Headers for hotel static-content endpoints (/api/xconnect/...).

    These use the separate Hotels-only account token and an
    `x-accept-language` hint, per the collection's hotel-static requests.
    """
    s = get_booking_api_settings()
    return {
        "Authorization": f"Bearer {s.hotel_static_bearer()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Trace-Id": _new_trace_id(),
        "x-accept-language": "en",
    }


def transfer_headers() -> dict[str, str]:
    """Headers for transfer endpoints (TransferList / TransferDetail).

    Transfer inventory is bound to the GT-018 account, so these carry the
    dedicated transfer token (falls back to the main token when unset).
    """
    s = get_booking_api_settings()
    return {
        "Authorization": f"Bearer {s.transfer_bearer()}",
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
        # The N8N-Technoheven V1 collection sends X-API-Key alongside the Bearer
        # token on flight search. The collection's value is the Postman {{token}}
        # variable (the same agent token), so mirror that.
        "X-API-Key": s.token,
    }


def currency_roe_headers() -> dict[str, str]:
    """Headers for /api/Currency/ROE/{code}.

    Per the Postman collection this endpoint authenticates with an antiforgery
    `RequestVerificationToken` rather than the Bearer token. That token expires,
    so it's supplied via env (BOOKING_ROE_VERIFICATION_TOKEN). When it's unset we
    fall back to the Bearer token so a single-credential setup still attempts the
    call; if the server rejects it, the caller falls back to the manual FX rate.
    """
    s = get_booking_api_settings()
    headers = {
        "accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "X-Trace-Id": _new_trace_id(),
    }
    if s.roe_verification_token:
        headers["RequestVerificationToken"] = s.roe_verification_token
    else:
        headers["Authorization"] = f"Bearer {s.token}"
    return headers


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
