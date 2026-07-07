"""Direct API timing — no LLM, just the raw flight search call."""
import sys, time, json
sys.path.insert(0, ".")

from booking_api.endpoints import call_flight_search
from booking_api.http_client import get_http_request_log, latest_http_seq

seq = latest_http_seq()
t = time.perf_counter()

try:
    result = call_flight_search(
        origin_iata="BOM",
        destination_iata="DXB",
        departure_date="2026-08-03",
        return_date="2026-08-07",
        adults=1,
    )
    elapsed = (time.perf_counter() - t) * 1000
    print(f"Total Python call time : {elapsed:.0f}ms")

    calls = [r for r in get_http_request_log() if r["seq"] > seq]
    for c in calls:
        print(f"  HTTP recorded        : {c['duration_ms']:.0f}ms | status: {c['status_code']}")

    response_size_kb = len(json.dumps(result)) / 1024
    print(f"  Response size        : {response_size_kb:.1f} KB")
    print(f"  success              : {result.get('success')}")

    data = result.get("data") or {}
    itins = data.get("pricedItineraries") or []
    print(f"  pricedItineraries    : {len(itins) if isinstance(itins, list) else repr(itins)[:80]}")

    if isinstance(itins, list) and itins:
        one = json.dumps(itins[0])
        print(f"  Size of 1 itinerary  : {len(one)/1024:.1f} KB")

except Exception as e:
    elapsed = (time.perf_counter() - t) * 1000
    print(f"ERROR after {elapsed:.0f}ms: {e}")
