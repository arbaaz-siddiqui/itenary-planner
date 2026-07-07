"""benchmark_all_apis.py — times every booking API endpoint.

Run:
    python benchmark_all_apis.py

Prints a table: API name | endpoint | time | response size | result count | status
"""
import json
import sys
import time

sys.path.insert(0, ".")

from booking_api.endpoints import (
    call_flight_search,
    call_hotel_availability,
    call_hotel_cities,
    call_hotel_static_by_city,
    call_hotel_static_data,
    call_hotel_property_info,
    call_hotel_descriptions,
    call_hotel_guest_review,
    call_tour_search,
    call_restaurant_search,
    call_visa_info,
    call_transfer_search,
    call_list_packages,
    call_entity_search,
    call_visa_countries,
    call_currency_roe,
)

results = []

def run(name, endpoint, fn):
    print(f"  testing {name}...", end=" ", flush=True)
    t = time.perf_counter()
    status = "OK"
    size_kb = 0
    count = "-"
    error = ""
    try:
        raw = fn()
        elapsed_ms = (time.perf_counter() - t) * 1000
        body = json.dumps(raw)
        size_kb = len(body) / 1024

        # Try to extract a meaningful count from the response
        if isinstance(raw, dict):
            for key in ["pricedItineraries", "Hotels", "TourList", "result", "data",
                        "PropertyInfo", "Hotels", "Restaurants", "Packages"]:
                val = raw.get(key)
                if isinstance(val, list):
                    count = str(len(val))
                    break
            if raw.get("success") is False:
                status = "NO_DATA"
                err = raw.get("error")
                if err:
                    if isinstance(err, list) and err:
                        error = str(err[0])[:60]
                    else:
                        error = str(err)[:60]
        elif isinstance(raw, list):
            count = str(len(raw))

    except Exception as e:
        elapsed_ms = (time.perf_counter() - t) * 1000
        status = "ERROR"
        error = str(e)[:60]

    results.append({
        "name": name,
        "endpoint": endpoint,
        "ms": elapsed_ms,
        "size_kb": size_kb,
        "count": count,
        "status": status,
        "error": error,
    })
    print(f"{elapsed_ms:.0f}ms")


print("\nRunning API benchmark (this will take a while for flight search)...\n")

# Public / no-auth
run("Entity Search (hotels)",   "/api/core/v1/search/hotels",
    lambda: call_entity_search(service="hotels", query="Dubai", size=10))

run("Entity Search (tours)",    "/api/core/v1/search/tours",
    lambda: call_entity_search(service="tours", query="Desert Safari", size=10))

run("Visa Countries",           "/api/visa/v1/countries",
    lambda: call_visa_countries())

run("Currency ROE",             "/api/Currency/ROE/INR",
    lambda: call_currency_roe(target_currency="INR"))

# Hotel static (Hotels-only token)
run("Hotel Cities",             "/api/xconnect/GetCitiesWithHotel",
    lambda: call_hotel_cities(city_name="Dubai"))

run("Hotel Static by City",     "/api/xconnect/GetStaticDataByCity",
    lambda: call_hotel_static_by_city(city_id=6))

run("Hotel Static Data",        "/api/xconnect/GetHotelStaticDataOptimize",
    lambda: call_hotel_static_data(hotel_ids=[384, 217, 100]))

run("Hotel Property Info",      "/api/xconnect/gethotelstaticdatalistsuboptimize_v1_Address",
    lambda: call_hotel_property_info(hotel_ids=[384]))

run("Hotel Descriptions",       "/api/xconnect/GetPropertyDescriptions",
    lambda: call_hotel_descriptions(hotel_ids=[384]))

run("Hotel Guest Review",       "/api/xconnect/GetHotelGuestReview",
    lambda: call_hotel_guest_review(hotel_id=384))

# Booking APIs (main token)
run("Hotel Availability",       "/api/xconnect/Availabilitywithcancellation",
    lambda: call_hotel_availability(
        hotel_ids=[384, 217],
        city_id=6,
        check_in="2026-07-29",
        check_out="2026-08-01",
        adults=2,
    ))

run("Visa Info",                "/api/visa/v1/visas",
    lambda: call_visa_info(country_id=213, nationality_id=101, travel_date="2026-07-29"))

run("Tour Search",              "/api/v1/tourservices/TourSearch/toursearchlist",
    lambda: call_tour_search(country_id=229, city_id=6, travel_date="2026-07-29"))

run("Restaurant Search",        "/api/restaurant/v1/restaurants",
    lambda: call_restaurant_search(city_id=244520, search_date="2026-07-29"))

run("Transfer Search",          "/api/transferservices/TransferList",
    lambda: call_transfer_search(
        from_lat=25.2515, from_lng=55.3683,
        to_lat=25.2030, to_lng=55.2790,
        from_place_id="ChIJaQ4mkwZdXz4R6e5IegDUleY",
        to_place_id="ChIJ4bjdQYNCXz4RUN7vxDANHsU",
        departure_date="2026-07-29",
        departure_time="15:00:00",
    ))

run("Package List",             "/api/staticpackageservices/staticpackage/packagelist",
    lambda: call_list_packages(country_id=229, city_id=6))

run("Flight Search",            "/api/Flight/search",
    lambda: call_flight_search(
        origin_iata="BOM",
        destination_iata="DXB",
        departure_date="2026-07-29",
        return_date="2026-08-05",
        adults=1,
    ))

# Print table
print()
print("-" * 110)
print(f"{'API':<28} {'Endpoint':<52} {'Time':>7} {'Size':>8} {'Count':>6}  {'Status'}")
print("-" * 110)
for r in results:
    t_str = f"{r['ms']:.0f}ms"
    s_str = f"{r['size_kb']:.1f}KB" if r['size_kb'] > 0 else "-"
    status_str = r['status']
    if r['error']:
        status_str += f" ({r['error'][:35]})"
    print(f"{r['name']:<28} {r['endpoint']:<52} {t_str:>7} {s_str:>8} {r['count']:>6}  {status_str}")
print("-" * 110)

# Summary
ok = [r for r in results if r['status'] == 'OK']
slow = [r for r in results if r['ms'] > 5000]
errors = [r for r in results if r['status'] == 'ERROR']
print(f"\nTotal APIs tested : {len(results)}")
print(f"OK                : {len(ok)}")
print(f"Slow (>5s)        : {len(slow)}")
print(f"Errors            : {len(errors)}")
if slow:
    print(f"\nSlow APIs:")
    for r in slow:
        print(f"  {r['name']}: {r['ms']:.0f}ms  ({r['size_kb']:.1f}KB)")
