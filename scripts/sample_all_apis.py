"""sample_all_apis.py

Standalone script. Hits every endpoint in the Technoheaven/Gujju Tours
Postman collection (N8N-Technoheven V1) and writes ALL responses to a
SINGLE JSON file you can share back for parser design.

Run:
    pip install requests
    python sample_all_apis.py

Output:
    api_samples.json  (one file, all endpoints inside)

Output shape:
{
  "_meta": {
    "generated_at": "...",
    "base_url": "...",
    "duration_seconds": 12.4,
    "endpoints_succeeded": 11,
    "endpoints_failed": 3
  },
  "endpoints": {
    "VisaList":     { "request": {...}, "response": {...}, "status_code": 200, "ok": true,  "latency_ms": 1234 },
    "FlightSearch": { "request": {...}, "response": {...}, "status_code": 500, "ok": false, "error": "...", "latency_ms": 30000 },
    ...
  }
}
"""

from __future__ import annotations

import json
import sys
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not installed.")
    print("Run: pip install requests")
    sys.exit(1)


# =============================================================================
# CREDENTIALS — extracted from N8N-Technoheven V1 postman collection
# =============================================================================
BASE_URL = "https://stagingapi.gujjutours.com"

# JWT bearer token (expires 2027-05-25)
TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhZ2VudElkIjoyMSwiYWdlbnRDb2RlIjoiR1QtMDIxIiwidXNlck5hbWUiOiJwaW5ha2luIiwi"
    "ZW1haWwiOiJwaW5ha2lucGF0ZWwzMDc3QGdtYWlsLmNvbSIsInBob25lTm8iOiIrOTEgMzU0Nzk4"
    "NDY1NDU2IiwidXNlcklkIjoyMSwic3ViVXNlcklkIjowLCJ1c2VyVHlwZUlkIjoxLCJ1c2VyVHlw"
    "ZSI6IkFnZW50IiwidXNlckNvZGUiOiJBZ2VudCIsInBheW1lbnRUeXBlIjoxLCJwYXltZW50VHlw"
    "ZVZhbHVlIjoiUHJlUGFpZCIsImRpc3BsYXlDdXJyZW5jeUlkIjoyLCJjcmVkaXRsaW1pdEN1cnJl"
    "bmN5SWQiOjIsImNyZWRpdGxpbWl0Q3VycmVuY3lDb2RlIjoiSU5SIiwicmF0ZUNhdGVnb3J5SWQi"
    "OjIsInRlbmFudElkIjoiQTI5Q0QzRUUtRDA1MC1BMzRBLTNBNTMtM0EyMEU0RkFGNUYzIiwiaXNG"
    "cm9tQXBpIjpmYWxzZSwic2Vzc2lvbklkIjoiYTNhYTA0YjUtZWI5MC00ZmFlLWI5ODktOTU3MTY3"
    "MjRiZmRiIiwiYXNzaXN0ZWRBZ2VudElkIjowLCJzZXJ2aWNlVHlwZSI6WyJIb3RlbHMiLCJGbGln"
    "aHQiLCJQYWNrYWdlcyIsIkNhciBSZW50YWwiLCJSZXN0YXVyYW50IiwiQWN0aXZpdGllcyIsIlRy"
    "YW5zZmVyIiwiVmlzYSJdLCJyZXdhcmRQb2ludHMiOltdLCJwZXJtaXNzaW9uVHlwZSI6W10sImlz"
    "cyI6IlRIX0EyOUNEM0VFLUQwNTAtQTM0QS0zQTUzLTNBMjBFNEZBRjVGMyIsImlhdCI6MTc3OTcw"
    "NDAzNCwiZXhwIjoxODExMjQwMDM0fQ."
    "bxLHcsXtxHkletmud0o3Ar-UUQR3dvet6iJZNJ0GAW4"
)

# Three tenant IDs visible in the collection
TENANT_ID_ACCOUNT = "A29CD3EE-D050-A34A-3A53-3A20E4FAF5F3"
TENANT_ID_FLIGHT_LIST = "E1047144-1A17-A2D5-E474-3A1DFEF15B7F"
TENANT_ID_FLIGHT_SEARCH = "DB1EC027-BDEC-3EA4-EDE7-3A1BE86F63F6"

REQUEST_TIMEOUT = 60  # seconds per request


# =============================================================================
# Header builders
# =============================================================================
def _trace_id() -> str:
    return str(uuid.uuid4())


def standard_headers() -> dict[str, str]:
    """For visa, restaurant, tour, transfer, package, hotel."""
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "X-Trace-Id": _trace_id(),
    }


def flight_list_headers() -> dict[str, str]:
    """For /api/Flight/getflightdetails."""
    return {
        **standard_headers(),
        "X-Tenant-Id": TENANT_ID_FLIGHT_LIST,
        "X-Custom-Host": "newinstance.activitylinker.com",
        "X-Site-Type": "B2B",
        "X-Time-Zone": "Arabian Standard Time",
        "X-Accept-Language": "gu",
    }


def flight_search_headers() -> dict[str, str]:
    """For /api/Flight/search."""
    return {
        **standard_headers(),
        "X-Tenant-Id": TENANT_ID_FLIGHT_SEARCH,
        "X-Site-Type": "B2B",
        "X-Time-Zone": "Arabian Standard Time",
        "X-Accept-Language": "ar",
        "accept": "text/plain",
    }


# =============================================================================
# Endpoint definitions — exact payloads from the Postman collection
# =============================================================================
def make_endpoints() -> list[dict[str, Any]]:
    """Return list of endpoint specs. Each spec has:
    name, method, path, headers, body (or None for GET).
    """
    return [
        # ------------------------------------------------------------------
        # 1. VisaList — POST /api/visa/v1/visas
        # ------------------------------------------------------------------
        {
            "name": "VisaList",
            "method": "POST",
            "path": "/api/visa/v1/visas",
            "headers": standard_headers(),
            "body": {
                "countryId": 213,
                "nationalityId": 213,
                "citizenId": 213,
                "visaTypeId": 1,
                "checkInDate": "10-10-2026",
                "guestInfo": {"adults": 1, "children": 1},
                "agentMarkupType": 0,
                "agentMarkup": 0,
                "UrlPath": "/en/visas/united-arab-emirates-213",
            },
        },
        # ------------------------------------------------------------------
        # 2. RestaurantList — POST /api/restaurant/v1/restaurants
        # ------------------------------------------------------------------
        {
            "name": "RestaurantList",
            "method": "POST",
            "path": "/api/restaurant/v1/restaurants",
            "headers": standard_headers(),
            "body": {
                "cityid": 244520,
                "GuestInfo": {"Adults": 1, "Children": 0},
                "SearchDate": "06-06-2026",
            },
        },
        # ------------------------------------------------------------------
        # 3. RestaurantDetails — POST /api/restaurant/v1/restaurants/3
        # ------------------------------------------------------------------
        {
            "name": "RestaurantDetails",
            "method": "POST",
            "path": "/api/restaurant/v1/restaurants/3",
            "headers": standard_headers(),
            "body": {
                "cityid": 244520,
                "GuestInfo": {"Adults": 1, "Children": 0},
                "SearchDate": "06-06-2026",
            },
        },
        # ------------------------------------------------------------------
        # 4. PackageList — POST /api/staticpackageservices/staticpackage/packagelist
        # ------------------------------------------------------------------
        {
            "name": "PackageList",
            "method": "POST",
            "path": "/api/staticpackageservices/staticpackage/packagelist",
            "headers": standard_headers(),
            "body": {
                "cityID": 0,
                "nationality": "India",
                "residency": "India",
                "checkInDate": "2026-06-19",
                "checkOutDate": "2026-06-23",
                "noofNights": 4,
                "rooms": [
                    {
                        "roomNo": 1,
                        "roomName": "Standard",
                        "roomType": "Standard",
                        "noofAdults": 2,
                        "noofChild": 0,
                        "child1Age": 0,
                        "child2Age": 0,
                    }
                ],
                "countryId": "213",
                "packageId": 0,
                "livingName": "",
                "regionId": 0,
                "AgentMarkup": 0,
            },
        },
        # ------------------------------------------------------------------
        # 5. PackageRate — POST /api/staticpackageservices/staticpackage/packagerate
        # ------------------------------------------------------------------
        {
            "name": "PackageRate",
            "method": "POST",
            "path": "/api/staticpackageservices/staticpackage/packagerate",
            "headers": standard_headers(),
            "body": {
                "cityID": 0,
                "nationality": "India",
                "residency": "India",
                "checkInDate": "2026-06-19",
                "checkOutDate": "2026-06-23",
                "noofNights": 4,
                "rooms": [
                    {
                        "roomNo": 1,
                        "roomName": "Standard",
                        "roomType": "Standard",
                        "noofAdults": 2,
                        "noofChild": 0,
                        "child1Age": 0,
                        "child2Age": 0,
                    }
                ],
                "countryId": "0",
                "packageId": 2,
                "livingName": "",
                "regionId": 0,
                "AgentMarkup": 0,
            },
        },
        # ------------------------------------------------------------------
        # 6. PackageStaticData — GET /api/.../packagestaticdata?PackageId=1
        # ------------------------------------------------------------------
        {
            "name": "PackageStaticData",
            "method": "GET",
            "path": "/api/staticpackageservices/staticpackage/packagestaticdata?PackageId=1",
            "headers": standard_headers(),
            "body": None,
        },
        # ------------------------------------------------------------------
        # 7. FlightList (details) — POST /api/Flight/getflightdetails
        # NOTE: needs a real fareSourceCode from a prior FlightSearch result.
        # The Postman collection's hardcoded value will probably 404 / 500.
        # That's still useful — it tells us the error shape.
        # ------------------------------------------------------------------
        {
            "name": "FlightList",
            "method": "POST",
            "path": "/api/Flight/getflightdetails",
            "headers": flight_list_headers(),
            "body": {
                "serviceName": "flightdetails",
                "itinerarySourceCode": (
                    "MSQkMCQkQW1hZGV1cyQkZmFkZGM2M2UtOGFkNy00ZTIyLThlMzQtZjNkZWU3NjNhNjZm"
                ),
                "fareSourceCode": (
                    "MSQkMCQkQW1hZGV1cyQkZmFkZGM2M2UtOGFkNy00ZTIyLThlMzQtZjNkZWU3NjNhNjZm"
                ),
                "conversationId": "faddc63e-8ad7-4e22-8e34-f3dee763a66f",
                "target": "test",
                "guestUserId": 0,
            },
        },
        # ------------------------------------------------------------------
        # 8. TourList — POST /api/v1/tourservices/TourSearch/toursearchlist
        # ------------------------------------------------------------------
        {
            "name": "TourList",
            "method": "POST",
            "path": "/api/v1/tourservices/TourSearch/toursearchlist",
            "headers": standard_headers(),
            "body": {
                "countryId": 213,
                "cityID": 244520,
                "tourCategoryId": 1,
                "transferTypeID": 0,
                "tourIDs": "",
                "travelDate": "2026-05-20",
            },
        },
        # ------------------------------------------------------------------
        # 9. TourListrate — POST /api/v1/tourservices/TourSearch/toursearchlistrate
        # ------------------------------------------------------------------
        {
            "name": "TourListrate",
            "method": "POST",
            "path": "/api/v1/tourservices/TourSearch/toursearchlistrate",
            "headers": standard_headers(),
            "body": {
                "countryId": 213,
                "cityID": 244520,
                "tourCategoryId": 1,
                "transferTypeID": 0,
                "tourIDs": "",
                "travelDate": "2026-05-20",
            },
        },
        # ------------------------------------------------------------------
        # 10. TourDetails — GET /api/v1/tourservices/TourSearch/Tourdetails?TourId=1347
        # ------------------------------------------------------------------
        {
            "name": "TourDetails",
            "method": "GET",
            "path": "/api/v1/tourservices/TourSearch/Tourdetails?TourId=1347",
            "headers": standard_headers(),
            "body": None,
        },
        # ------------------------------------------------------------------
        # 11. TransferList — POST /api/transferservices/TransferList
        # ------------------------------------------------------------------
        {
            "name": "TransferList",
            "method": "POST",
            "path": "/api/transferservices/TransferList",
            "headers": standard_headers(),
            "body": {
                "fromLongitude": 55.3683066,
                "fromLatitude": 25.2515401,
                "toLongitude": 55.3032906,
                "toLatitude": 25.2145565,
                "departureDate": "2026-06-25",
                "departureTime": "07:00:00",
                "returnDate": "2026-06-27",
                "returnTime": "07:00:00",
                "isRoundTrip": 0,
                "fromType": "A",
                "toType": "O",
                "fromPlaceId": "ChIJaQ4mkwZdXz4R6e5IegDUleY",
                "toPlaceId": "ChIJB1zIKShoXz4RnbaTPPup7aU",
                "TransferRateTypes": [
                    {
                        "TransferRateTypeId": 1,
                        "Count": 1,
                        "transferRateTypeName": "Adult",
                    }
                ],
                "agtMkp": 0,
                "agtMkpType": 0,
            },
        },
        # ------------------------------------------------------------------
        # 12. TransferDetails — POST /api/transferservices/TransferDetail
        # NOTE: hardcoded uniqueKey from Postman; likely will 404 unless a
        # prior TransferList was just run. Still useful for error shape.
        # ------------------------------------------------------------------
        {
            "name": "TransferDetails",
            "method": "POST",
            "path": "/api/transferservices/TransferDetail",
            "headers": standard_headers(),
            "body": {
                "fromLongitude": 55.3683066,
                "fromLatitude": 25.2515401,
                "toLongitude": 55.3032906,
                "toLatitude": 25.2145565,
                "departureDate": "2026-06-25",
                "departureTime": "07:00:00",
                "returnDate": "2026-06-27",
                "returnTime": "07:00:00",
                "isRoundTrip": 0,
                "fromType": "A",
                "toType": "O",
                "fromPlaceId": "ChIJaQ4mkwZdXz4R6e5IegDUleY",
                "toPlaceId": "ChIJB1zIKShoXz4RnbaTPPup7aU",
                "uniqueKey": (
                    "4tqcvYdHNKtl2ewJS3A2nGK8ZJsLkmAdkO0njhFD0npmgnWUXbeB9"
                    "RNtwjc292sxfdo+W9/FdXuxSVTL4CeYYw=="
                ),
                "TransferRateTypes": [
                    {
                        "TransferRateTypeId": 1,
                        "Count": 1,
                        "transferRateTypeName": "Adult",
                    }
                ],
                "agtMkp": 0,
                "agtMkpType": 0,
            },
        },
        # ------------------------------------------------------------------
        # 13. FlightSearch — POST /api/Flight/search
        # ------------------------------------------------------------------
        {
            "name": "FlightSearch",
            "method": "POST",
            "path": "/api/Flight/search",
            "headers": flight_search_headers(),
            "body": {
                "OriginDestinationInformations": [
                    {
                        "DepartureDateTime": "11-07-2026",
                        "OriginLocationCode": "DEL",
                        "DestinationLocationCode": "BOM",
                    }
                ],
                "TravelPreferences": {
                    "MaxStopsQuantity": "1",
                    "CabinPreference": "Y",
                    "AirTripType": "oneway",
                },
                "PricingSourceType": "all",
                "PassengerTypeQuantities": [{"Code": "ADT", "Quantity": 1}],
                "childAge": "",
                "infantAge": "",
                "Target": "test",
                "agentID": 0,
                "rateCategoryId": 0,
                "supplierTime": "6",
                "supplierId": 0,
                "suppliers": [],
                "isMobile": 0,
                "AirlineName": "",
            },
        },
        # ------------------------------------------------------------------
        # 14. HotelSearch — POST /api/xconnect/Availabilitywithcancellation
        # ------------------------------------------------------------------
        {
            "name": "HotelSearch",
            "method": "POST",
            "path": "/api/xconnect/Availabilitywithcancellation",
            "headers": standard_headers(),
            "body": {
                "Token": "",
                "Request": {
                    "Rooms": [
                        {
                            "RoomNo": 1,
                            "NoofAdults": 2,
                            "NoOfChild": 0,
                            "ChildAge": [],
                        }
                    ],
                    "CityID": "244520",
                    "CheckInDate": "11-20-2026",
                    "CheckOutDate": "11-22-2026",
                    "NoofNights": "2",
                    "Nationality": "India",
                    "Filters": {
                        "IsRecommendedOnly": "0",
                        "IsShowRooms": "1",
                        "IsOnlyAvailable": "1",
                        "StarRating": {"Min": 1, "Max": 5},
                        "HotelIDs": "509,206",
                    },
                },
                "AdvancedOptions": {
                    "Currency": "AED",
                    "CustomerIpAddress": "111",
                    "HotelName": "",
                },
                "IsMobile": 1,
                "IsSearch": 1,
            },
        },
    ]


# =============================================================================
# Console helpers
# =============================================================================
def c(text: str, color: str) -> str:
    codes = {"green": "32", "red": "31", "yellow": "33", "cyan": "36", "gray": "90"}
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def call_endpoint(spec: dict[str, Any]) -> dict[str, Any]:
    """Call one endpoint. Return a record with request + response."""
    name = spec["name"]
    method = spec["method"]
    path = spec["path"]
    url = BASE_URL.rstrip("/") + ("" if path.startswith("/") else "/") + path

    print(c(f"\n→ [{name}]", "cyan"), c(f"{method} {path}", "gray"))

    record: dict[str, Any] = {
        "request": {
            "method": method,
            "url": url,
            "headers": {
                # Strip the long token for readability in the saved JSON
                k: ("Bearer <JWT_REDACTED>" if k.lower() == "authorization" else v)
                for k, v in spec["headers"].items()
            },
            "body": spec.get("body"),
        },
    }

    started = time.perf_counter()
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=spec["headers"], timeout=REQUEST_TIMEOUT)
        else:
            response = requests.request(
                method,
                url,
                headers=spec["headers"],
                json=spec.get("body"),
                timeout=REQUEST_TIMEOUT,
            )
    except requests.Timeout:
        elapsed = time.perf_counter() - started
        print(c(f"  ✗ TIMEOUT after {elapsed:.1f}s", "red"))
        return {
            **record,
            "ok": False,
            "error_type": "Timeout",
            "error": f"Request timed out after {REQUEST_TIMEOUT}s",
            "latency_ms": int(elapsed * 1000),
        }
    except requests.RequestException as e:
        elapsed = time.perf_counter() - started
        print(c(f"  ✗ NETWORK ERROR: {e}", "red"))
        return {
            **record,
            "ok": False,
            "error_type": "NetworkError",
            "error": str(e),
            "latency_ms": int(elapsed * 1000),
        }
    except Exception as e:
        elapsed = time.perf_counter() - started
        print(c(f"  ✗ EXCEPTION: {type(e).__name__}: {e}", "red"))
        return {
            **record,
            "ok": False,
            "error_type": type(e).__name__,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "latency_ms": int(elapsed * 1000),
        }

    elapsed = time.perf_counter() - started
    latency_ms = int(elapsed * 1000)
    sc = response.status_code
    ok_http = 200 <= sc < 300

    # Try to parse response as JSON; if not, save the raw text
    try:
        body_parsed: Any = response.json()
        body_kind = "json"
    except ValueError:
        body_parsed = response.text[:5000]
        body_kind = "text"

    # Show summary
    if ok_http:
        size_hint = ""
        if isinstance(body_parsed, dict):
            size_hint = f"keys={list(body_parsed.keys())[:6]}"
        elif isinstance(body_parsed, list):
            size_hint = f"list len={len(body_parsed)}"
        print(c(f"  ✓ {sc} OK · {latency_ms}ms · {size_hint}", "green"))
    else:
        # Pull a useful error snippet
        snippet = ""
        if isinstance(body_parsed, dict):
            for k in ("message", "Message", "error", "Error", "errorMessage"):
                v = body_parsed.get(k)
                if isinstance(v, str):
                    snippet = v[:120]
                    break
                if isinstance(v, dict):
                    snippet = json.dumps(v)[:120]
                    break
        else:
            snippet = str(body_parsed)[:120]
        print(c(f"  ✗ {sc} · {latency_ms}ms · {snippet}", "red"))

    return {
        **record,
        "ok": ok_http,
        "status_code": sc,
        "latency_ms": latency_ms,
        "response_kind": body_kind,
        "response": body_parsed,
        "response_size_bytes": len(response.content),
    }


# =============================================================================
# Main
# =============================================================================
def main() -> int:
    print(c("\n╔════════════════════════════════════════════════════════════╗", "cyan"))
    print(c("║  Gujju Tours / Technoheaven — API Sampler                 ║", "cyan"))
    print(c("║  Hits all 14 endpoints, saves to api_samples.json         ║", "cyan"))
    print(c("╚════════════════════════════════════════════════════════════╝", "cyan"))
    print(f"\nBase URL: {BASE_URL}")
    print("Token expiry: 2027-05-25 (per JWT claim)\n")

    started_at = datetime.now(UTC)
    overall_start = time.perf_counter()

    endpoints = make_endpoints()
    print(f"Calling {len(endpoints)} endpoints with {REQUEST_TIMEOUT}s timeout each…")

    results: dict[str, Any] = {}
    for spec in endpoints:
        results[spec["name"]] = call_endpoint(spec)

    overall_elapsed = time.perf_counter() - overall_start

    succeeded = sum(1 for r in results.values() if r.get("ok"))
    failed = len(results) - succeeded

    output = {
        "_meta": {
            "generated_at": started_at.isoformat(),
            "base_url": BASE_URL,
            "duration_seconds": round(overall_elapsed, 2),
            "endpoint_count": len(results),
            "endpoints_succeeded": succeeded,
            "endpoints_failed": failed,
            "token_expiry_iso": "2027-05-25T10:13:54+00:00",
            "tenant_id_account": TENANT_ID_ACCOUNT,
            "tenant_id_flight_list": TENANT_ID_FLIGHT_LIST,
            "tenant_id_flight_search": TENANT_ID_FLIGHT_SEARCH,
            "notes": (
                "FlightList (getflightdetails) needs a fresh fareSourceCode from "
                "a prior FlightSearch call. TransferDetails needs a fresh uniqueKey "
                "from TransferList. Postman's hardcoded values likely 404/500 — "
                "that's still useful for error-shape inspection."
            ),
        },
        "endpoints": results,
    }

    # Write to repo root regardless of where the script is invoked from
    repo_root = Path(__file__).resolve().parent.parent
    out_path = repo_root / "api_samples.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(c("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "cyan"))
    print(c(f"  Total: {len(results)} endpoints in {overall_elapsed:.1f}s", "cyan"))
    print(c(f"  ✓ {succeeded} succeeded", "green"))
    if failed:
        print(c(f"  ✗ {failed} failed (still saved; inspect for error shape)", "red"))

    size_mb = out_path.stat().st_size / 1_000_000
    print(c(f"\n  Saved → {out_path.resolve()}  ({size_mb:.2f} MB)", "cyan"))
    print(c("\n  Share this file back. I'll use it to build the parsers.\n", "cyan"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
