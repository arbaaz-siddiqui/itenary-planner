"""End-to-end diagnostic — hits every booking endpoint with realistic
Dubai test data. Saves raw responses to `data/samples/<NAME>.json` for
inspection AND for use as test fixtures.

Output structure:
    data/samples/
        REQUESTS.json       <-- summary: what was sent for each
        RESPONSES_SUMMARY.json   <-- summary: success/empty/error per endpoint
        FLIGHT_SEARCH.json
        FLIGHT_DETAILS.json
        HOTEL_SEARCH.json
        TOUR_LIST.json
        TOUR_RATES.json
        TOUR_DETAILS.json
        TRANSFER_LIST.json
        TRANSFER_DETAILS.json
        RESTAURANT_LIST.json
        RESTAURANT_DETAILS.json
        VISA.json
        PACKAGE_LIST.json
        PACKAGE_RATES.json
        PACKAGE_STATIC_DATA.json

The order matters: search endpoints run first because their results feed
the detail endpoints. If a search returns nothing, the corresponding
detail call is skipped with a clear reason.

Run:
    python -m scripts.diagnose_apis
    python -m scripts.diagnose_apis --quick       # skip details
    python -m scripts.diagnose_apis --only flight,hotel
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent import configure_logging
from booking_api import (
    call_flight_details,
    call_flight_search,
    call_hotel_availability,
    call_list_packages,
    call_package_rates,
    call_package_static_data,
    call_restaurant_details,
    call_restaurant_search,
    call_tour_details,
    call_tour_rates,
    call_tour_search,
    call_transfer_details,
    call_transfer_search,
    call_visa_info,
)
from core import TripPlannerError, nights_between
from settings import get_booking_api_settings

SAMPLES_DIR = Path("data/samples")
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

# Where to save the request payloads + summary
REQUESTS_OUT = SAMPLES_DIR / "REQUESTS.json"
SUMMARY_OUT = SAMPLES_DIR / "RESPONSES_SUMMARY.json"

# Test inputs — modify here if needed
TEST_INPUTS = {
    "origin_iata": "DEL",
    "destination_iata": "DXB",
    "country_id_uae": 213,
    "country_id_india": 105,
    "city_id_dubai": 244520,
    "hotel_ids": [206, 509],
    "nationality": "India",
    "dubai_airport": {
        "lat": 25.2532,
        "lng": 55.3657,
        "place_id": "ChIJaQ4mkwZdXz4R6e5IegDUleY",  # client's sample
    },
    "dubai_hotel_location": {
        "lat": 25.2145565,
        "lng": 55.3032906,
        "place_id": "ChIJB1zIKShoXz4RnbaTPPup7aU",  # client's sample
    },
}


def _color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def _ok(msg: str) -> None:
    print(_color(f"  ✓ {msg}", "32"))


def _warn(msg: str) -> None:
    print(_color(f"  ⚠ {msg}", "33"))


def _fail(msg: str) -> None:
    print(_color(f"  ✗ {msg}", "31"))


def _header(name: str) -> None:
    print()
    print(_color(f"━━━ {name} " + "━" * max(0, 60 - len(name)), "36"))


def _save(name: str, data: Any) -> None:
    path = SAMPLES_DIR / f"{name}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)
    print(f"    saved → {path}")


def _summarize_response(data: Any) -> dict[str, Any]:
    """Generate a short summary of a response for the human reader."""
    if not isinstance(data, dict):
        return {"type": type(data).__name__}

    summary: dict[str, Any] = {"top_keys": list(data.keys())[:10]}

    # Common patterns in this API family
    for path_candidates in [
        ["result"],
        ["data"],
        ["AvailabilityRS", "HotelResult"],
        ["data", "pricedItineraries"],
        ["result", "list"],
        ["result", "tourStaticlists"],
        ["result", "visaOptions"],
    ]:
        cur: Any = data
        for key in path_candidates:
            if isinstance(cur, dict):
                cur = cur.get(key)
            else:
                cur = None
                break
        if isinstance(cur, list):
            summary[f"{'.'.join(path_candidates)}_count"] = len(cur)
            if cur:
                first = cur[0]
                if isinstance(first, dict):
                    summary[f"{'.'.join(path_candidates)}_first_keys"] = list(first.keys())[:15]
        elif isinstance(cur, dict):
            summary[f"{'.'.join(path_candidates)}_keys"] = list(cur.keys())[:10]

    return summary


def _attempt(
    name: str,
    fn: Callable[..., dict[str, Any]],
    request_log: dict[str, Any],
    summary_log: dict[str, Any],
    **kwargs: Any,
) -> tuple[str, dict[str, Any] | None]:
    """Run one endpoint call. Save the response. Record summary."""
    _header(name)
    print(f"  args: {json.dumps(kwargs, default=str)[:400]}")
    request_log[name] = {"function": fn.__name__, "args": kwargs}

    try:
        result = fn(**kwargs)
    except TripPlannerError as e:
        _fail(f"{e.code}: {e.message}")
        err_data = e.to_dict()
        _save(name, err_data)
        summary_log[name] = {
            "status": "api_error",
            "error_type": err_data.get("error_type"),
            "code": err_data.get("code"),
            "message": err_data.get("message"),
            "server_message": err_data.get("server_message"),
            "status_code": err_data.get("status_code"),
        }
        return "api_error", None
    except Exception as e:
        _fail(f"EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()
        err_data = {"error_type": type(e).__name__, "message": str(e)}
        _save(name, err_data)
        summary_log[name] = {"status": "exception", **err_data}
        return "exception", None

    _save(name, result)
    summary = _summarize_response(result)
    summary_log[name] = {"status": "ok", **summary}
    _ok(f"Saved. Summary: {json.dumps(summary, default=str)[:300]}")
    return "ok", result


def _has_filter(only_filter: list[str] | None, *tags: str) -> bool:
    if not only_filter:
        return True
    return any(t in only_filter for t in tags)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip detail endpoints (faster; saves tokens)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help=("Comma-separated tags to run (flight,hotel,tour,transfer,restaurant,visa,package)"),
    )
    parser.add_argument(
        "--days-out",
        type=int,
        default=60,
        help="Test window starts this many days from today",
    )
    parser.add_argument(
        "--nights",
        type=int,
        default=4,
        help="Test window length in nights",
    )
    args = parser.parse_args()

    only = [t.strip().lower() for t in args.only.split(",") if t.strip()] if args.only else None

    configure_logging(prod=False)

    print(_color("\n╔════════════════════════════════════════════════════╗", "36"))
    print(_color("║   Technoheaven API Diagnostic — staging probe     ║", "36"))
    print(_color("╚════════════════════════════════════════════════════╝\n", "36"))

    s = get_booking_api_settings()
    print(f"Base URL:                {s.base_url}")
    print(
        f"Token:                   {'set (' + str(len(s.token)) + ' chars)' if s.token else 'MISSING'}"
    )
    print(f"Account tenant:          {s.tenant_id}")
    print(f"Flight-search tenant:    {s.flight_search_tenant_id}")
    print(f"Flight-list tenant:      {s.flight_list_tenant_id}")
    print(f"Output dir:              {SAMPLES_DIR.resolve()}")
    if only:
        print(f"Filter (--only):         {only}")
    if args.quick:
        print("Mode:                    QUICK (skipping details)")

    if not s.token:
        _fail("\nBOOKING_TOKEN missing — set it in .env")
        return 1

    check_in = (datetime.now() + timedelta(days=args.days_out)).strftime("%Y-%m-%d")
    check_out = (datetime.now() + timedelta(days=args.days_out + args.nights)).strftime("%Y-%m-%d")
    nights = nights_between(check_in, check_out)
    print(f"\nTest window: {check_in} → {check_out} ({nights} nights)\n")

    request_log: dict[str, Any] = {
        "_meta": {
            "base_url": s.base_url,
            "test_window": {"check_in": check_in, "check_out": check_out, "nights": nights},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    }
    summary_log: dict[str, Any] = {}

    # ---------- VISA ----------
    if _has_filter(only, "visa"):
        _attempt(
            "VISA",
            call_visa_info,
            request_log,
            summary_log,
            country_id=TEST_INPUTS["country_id_uae"],
            nationality_id=TEST_INPUTS["country_id_india"],
            travel_date=check_in,
            adults=2,
            children=0,
        )

    # ---------- FLIGHTS ----------
    flight_search_result = None
    if _has_filter(only, "flight"):
        status, flight_search_result = _attempt(
            "FLIGHT_SEARCH",
            call_flight_search,
            request_log,
            summary_log,
            origin_iata=TEST_INPUTS["origin_iata"],
            destination_iata=TEST_INPUTS["destination_iata"],
            departure_date=check_in,
            return_date=check_out,
            adults=2,
        )
        # FlightDetails needs a fare_source_code from search
        if not args.quick and status == "ok" and flight_search_result is not None:
            fsc = _extract_first(flight_search_result, "fareSourceCode")
            isc = _extract_first(flight_search_result, "itinerarySourceCode")
            conv_id = _extract_first(flight_search_result, "conversationId")
            if fsc:
                _attempt(
                    "FLIGHT_DETAILS",
                    call_flight_details,
                    request_log,
                    summary_log,
                    fare_source_code=fsc,
                    itinerary_source_code=isc or fsc,
                    conversation_id=conv_id or "",
                )
            else:
                _warn("FLIGHT_DETAILS skipped — no fareSourceCode in search response")
                summary_log["FLIGHT_DETAILS"] = {"status": "skipped", "reason": "no fareSourceCode"}

    # ---------- HOTELS ----------
    if _has_filter(only, "hotel"):
        _attempt(
            "HOTEL_SEARCH",
            call_hotel_availability,
            request_log,
            summary_log,
            hotel_ids=TEST_INPUTS["hotel_ids"],
            city_id=TEST_INPUTS["city_id_dubai"],
            check_in=check_in,
            check_out=check_out,
            adults=2,
            children=0,
            nationality=TEST_INPUTS["nationality"],
        )

    # ---------- TOURS ----------
    tour_list_result = None
    if _has_filter(only, "tour"):
        status, tour_list_result = _attempt(
            "TOUR_LIST",
            call_tour_search,
            request_log,
            summary_log,
            country_id=TEST_INPUTS["country_id_uae"],
            city_id=TEST_INPUTS["city_id_dubai"],
            travel_date=check_in,
            tour_category_id=1,
        )
        _attempt(
            "TOUR_RATES",
            call_tour_rates,
            request_log,
            summary_log,
            country_id=TEST_INPUTS["country_id_uae"],
            city_id=TEST_INPUTS["city_id_dubai"],
            travel_date=check_in,
            tour_category_id=1,
        )
        if not args.quick and status == "ok" and tour_list_result is not None:
            tour_id = _extract_first(tour_list_result, "tourID")
            if tour_id:
                _attempt(
                    "TOUR_DETAILS",
                    call_tour_details,
                    request_log,
                    summary_log,
                    tour_id=int(tour_id),
                )
            else:
                _warn("TOUR_DETAILS skipped — no tourID in list response")
                summary_log["TOUR_DETAILS"] = {"status": "skipped", "reason": "no tourID"}

    # ---------- TRANSFERS ----------
    transfer_list_result = None
    if _has_filter(only, "transfer"):
        airport = TEST_INPUTS["dubai_airport"]
        hotel = TEST_INPUTS["dubai_hotel_location"]
        status, transfer_list_result = _attempt(
            "TRANSFER_LIST",
            call_transfer_search,
            request_log,
            summary_log,
            from_lat=airport["lat"],
            from_lng=airport["lng"],
            to_lat=hotel["lat"],
            to_lng=hotel["lng"],
            from_place_id=airport["place_id"],
            to_place_id=hotel["place_id"],
            departure_date=check_in,
            departure_time="07:00:00",
            return_date=check_out,
            return_time="07:00:00",
            is_round_trip=False,
            from_type="A",
            to_type="O",
            adults=1,
        )
        if not args.quick and status == "ok" and transfer_list_result is not None:
            unique_key = _extract_first(transfer_list_result, "uniqueKey")
            if unique_key:
                _attempt(
                    "TRANSFER_DETAILS",
                    call_transfer_details,
                    request_log,
                    summary_log,
                    from_lat=airport["lat"],
                    from_lng=airport["lng"],
                    to_lat=hotel["lat"],
                    to_lng=hotel["lng"],
                    from_place_id=airport["place_id"],
                    to_place_id=hotel["place_id"],
                    departure_date=check_in,
                    departure_time="07:00:00",
                    return_date=check_out,
                    return_time="07:00:00",
                    is_round_trip=False,
                    adults=1,
                    unique_key=unique_key,
                )
            else:
                _warn("TRANSFER_DETAILS skipped — no uniqueKey in list response")
                summary_log["TRANSFER_DETAILS"] = {"status": "skipped", "reason": "no uniqueKey"}

    # ---------- RESTAURANTS ----------
    restaurant_list_result = None
    if _has_filter(only, "restaurant"):
        status, restaurant_list_result = _attempt(
            "RESTAURANT_LIST",
            call_restaurant_search,
            request_log,
            summary_log,
            city_id=TEST_INPUTS["city_id_dubai"],
            search_date=check_in,
            adults=1,
            children=0,
        )
        if not args.quick and status == "ok" and restaurant_list_result is not None:
            rest_id = _extract_first(restaurant_list_result, "restaurantId")
            if rest_id:
                _attempt(
                    "RESTAURANT_DETAILS",
                    call_restaurant_details,
                    request_log,
                    summary_log,
                    restaurant_id=int(rest_id),
                    city_id=TEST_INPUTS["city_id_dubai"],
                    search_date=check_in,
                    adults=1,
                    children=0,
                )
            else:
                _warn("RESTAURANT_DETAILS skipped — no restaurantId in list response")
                summary_log["RESTAURANT_DETAILS"] = {
                    "status": "skipped",
                    "reason": "no restaurantId",
                }

    # ---------- PACKAGES ----------
    package_list_result = None
    if _has_filter(only, "package"):
        status, package_list_result = _attempt(
            "PACKAGE_LIST",
            call_list_packages,
            request_log,
            summary_log,
            country_id=TEST_INPUTS["country_id_uae"],
            city_id=TEST_INPUTS["city_id_dubai"],
            check_in=check_in,
            check_out=check_out,
            nights=nights,
            adults=2,
            children=0,
            nationality=TEST_INPUTS["nationality"],
            residency=TEST_INPUTS["nationality"],
        )
        if not args.quick and status == "ok" and package_list_result is not None:
            package_id = _extract_first(package_list_result, "packageId") or _extract_first(
                package_list_result, "packageID"
            )
            if package_id:
                _attempt(
                    "PACKAGE_RATES",
                    call_package_rates,
                    request_log,
                    summary_log,
                    package_id=int(package_id),
                    country_id=TEST_INPUTS["country_id_uae"],
                    city_id=TEST_INPUTS["city_id_dubai"],
                    check_in=check_in,
                    check_out=check_out,
                    nights=nights,
                    adults=2,
                    children=0,
                )
                _attempt(
                    "PACKAGE_STATIC_DATA",
                    call_package_static_data,
                    request_log,
                    summary_log,
                    package_id=int(package_id),
                )
            else:
                _warn("PACKAGE_RATES/STATIC_DATA skipped — no packageId in list response")
                summary_log["PACKAGE_RATES"] = {"status": "skipped", "reason": "no packageId"}
                summary_log["PACKAGE_STATIC_DATA"] = {"status": "skipped", "reason": "no packageId"}

    # ---------- SAVE SUMMARIES ----------
    with REQUESTS_OUT.open("w", encoding="utf-8") as f:
        json.dump(request_log, f, indent=2, default=str)
    with SUMMARY_OUT.open("w", encoding="utf-8") as f:
        json.dump(summary_log, f, indent=2, default=str)

    print()
    print(_color("━━━ SUMMARY " + "━" * 56, "36"))
    icon = {
        "ok": "✓",
        "empty": "⚠",
        "skipped": "·",
        "api_error": "✗",
        "exception": "✗",
    }
    color = {
        "ok": "32",
        "empty": "33",
        "skipped": "37",
        "api_error": "31",
        "exception": "31",
    }
    for endpoint, info in summary_log.items():
        status = info.get("status", "?")
        suffix = ""
        if status == "ok":
            n_keys = [k for k in info if k.endswith("_count")]
            if n_keys:
                suffix = f"  ({info[n_keys[0]]} items)"
        elif status in ("api_error", "exception"):
            suffix = f"  {info.get('code') or info.get('error_type', '?')}"
        elif status == "skipped":
            suffix = f"  ({info.get('reason', '?')})"
        print(
            _color(
                f"  {icon.get(status, '?')} {endpoint:22} {status}{suffix}",
                color.get(status, "0"),
            )
        )

    print(f"\nRequest log → {REQUESTS_OUT}")
    print(f"Summary log → {SUMMARY_OUT}")
    print(f"Raw responses → {SAMPLES_DIR}/<NAME>.json\n")

    n_ok = sum(1 for v in summary_log.values() if v.get("status") == "ok")
    n_total = len(summary_log)
    return 0 if n_ok == n_total else 1


def _extract_first(data: Any, field_name: str) -> Any:
    """Recursively find the first occurrence of a field name in a dict/list."""
    if isinstance(data, dict):
        if field_name in data and data[field_name] is not None:
            return data[field_name]
        for v in data.values():
            result = _extract_first(v, field_name)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _extract_first(item, field_name)
            if result is not None:
                return result
    return None


if __name__ == "__main__":
    sys.exit(main())
