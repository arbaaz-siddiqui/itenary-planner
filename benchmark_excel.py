"""benchmark_excel.py — hits every API, records full request/response, exports to Excel.

Run:
    python benchmark_excel.py

Outputs: api_benchmark_YYYYMMDD_HHMMSS.xlsx
Columns: API Name | Endpoint | Method | Headers | Request Body | Response Time (ms)
         Response Size (KB) | HTTP Status | Success | Result Count | Full Response | Error
"""
import json
import sys
import time
import datetime

sys.path.insert(0, ".")

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

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
    call_tour_details,
    call_restaurant_search,
    call_visa_info,
    call_transfer_search,
    call_list_packages,
    call_entity_search,
    call_visa_countries,
    call_currency_roe,
)
from booking_api.headers import (
    base_headers,
    flight_search_headers,
    flight_list_headers,
    hotel_static_headers,
    currency_roe_headers,
)
from booking_api.http_client import get_http_request_log, latest_http_seq
from settings import get_booking_api_settings

s = get_booking_api_settings()

# ── helpers ──────────────────────────────────────────────────────────────────

def _count(raw):
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        for k in ["pricedItineraries", "TourList", "Hotels", "result", "data",
                  "PropertyInfo", "Restaurants", "Packages", "rooms"]:
            v = raw.get(k)
            if isinstance(v, list):
                return len(v)
    return "-"


def run_api(name, endpoint, method, headers_fn, body, call_fn):
    print(f"  {name}...", end=" ", flush=True)
    seq = latest_http_seq()
    t0 = time.perf_counter()
    http_status = "-"
    success = "-"
    error_msg = ""
    raw = {}

    try:
        raw = call_fn()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # grab recorded HTTP status
        calls = [r for r in get_http_request_log() if r["seq"] > seq]
        if calls:
            http_status = str(calls[-1].get("status_code", "-"))

        if isinstance(raw, dict):
            s_val = raw.get("success")
            status_code = raw.get("statusCode")
            if s_val is True:
                success = "TRUE"
            elif s_val is False:
                success = "FALSE"
                err = raw.get("error")
                if isinstance(err, list) and err:
                    error_msg = str(err[0])
                elif err:
                    error_msg = str(err)[:200]
            elif status_code == 200 or http_status == "200":
                success = "TRUE"
            else:
                success = "TRUE"  # got a response, no explicit failure

    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        http_status = "ERR"
        success = "FALSE"
        error_msg = str(e)

    size_kb = round(len(json.dumps(raw)) / 1024, 2) if raw else 0
    count = _count(raw)
    print(f"{elapsed_ms:.0f}ms  {size_kb}KB  count={count}")

    return {
        "API Name": name,
        "Endpoint": endpoint,
        "Method": method,
        "Headers": json.dumps(headers_fn() if callable(headers_fn) else headers_fn, indent=2),
        "Request Body": json.dumps(body, indent=2) if body else "N/A (GET)",
        "Response Time (ms)": round(elapsed_ms),
        "Response Size (KB)": size_kb,
        "HTTP Status": http_status,
        "Success": success,
        "Result Count": count,
        "Full Response": json.dumps(raw, indent=2),
        "Error": error_msg,
    }


# ── define all APIs ───────────────────────────────────────────────────────────

BASE = "https://stagingapi.gujjutours.com"

APIS = []

# 1. Entity Search — Hotels
body = None
APIS.append(run_api(
    "Entity Search — Hotels",
    f"{BASE}/api/core/v1/search/hotels?q=Dubai&size=10",
    "GET",
    lambda: {"Accept": "application/json"},
    body,
    lambda: call_entity_search(service="hotels", query="Dubai", size=10),
))

# 2. Entity Search — Tours
APIS.append(run_api(
    "Entity Search — Tours",
    f"{BASE}/api/core/v1/search/tours?q=Desert+Safari&size=10",
    "GET",
    lambda: {"Accept": "application/json"},
    None,
    lambda: call_entity_search(service="tours", query="Desert Safari", size=10),
))

# 3. Entity Search — Restaurants
APIS.append(run_api(
    "Entity Search — Restaurants",
    f"{BASE}/api/core/v1/search/restaurants?q=Dubai&size=10",
    "GET",
    lambda: {"Accept": "application/json"},
    None,
    lambda: call_entity_search(service="restaurants", query="Dubai", size=10),
))

# 4. Entity Search — Airlines
APIS.append(run_api(
    "Entity Search — Airlines",
    f"{BASE}/api/core/v1/search/airlines?q=Emirates&size=10",
    "GET",
    lambda: {"Accept": "application/json"},
    None,
    lambda: call_entity_search(service="airlines", query="Emirates", size=10),
))

# 5. Visa Countries
APIS.append(run_api(
    "Visa Countries",
    f"{BASE}/api/visa/v1/countries",
    "GET",
    base_headers,
    None,
    lambda: call_visa_countries(),
))

# 6. Currency ROE
APIS.append(run_api(
    "Currency ROE",
    f"{BASE}/api/Currency/ROE/INR",
    "GET",
    currency_roe_headers,
    None,
    lambda: call_currency_roe(target_currency="INR"),
))

# 7. Hotel Cities
b7 = {"Request": {"CityName": "Dubai"}}
APIS.append(run_api(
    "Hotel Cities",
    f"{BASE}/api/xconnect/GetCitiesWithHotel",
    "POST",
    hotel_static_headers,
    b7,
    lambda: call_hotel_cities(city_name="Dubai"),
))

# 8. Hotel Static by City
b8 = {"Request": {"CityID": "6", "Type": "city", "LocationId": ""}}
APIS.append(run_api(
    "Hotel Static by City",
    f"{BASE}/api/xconnect/GetStaticDataByCity",
    "POST",
    hotel_static_headers,
    b8,
    lambda: call_hotel_static_by_city(city_id=6),
))

# 9. Hotel Static Data
b9 = {"HotelIDs": "384,217,100"}
APIS.append(run_api(
    "Hotel Static Data",
    f"{BASE}/api/xconnect/GetHotelStaticDataOptimize",
    "POST",
    hotel_static_headers,
    b9,
    lambda: call_hotel_static_data(hotel_ids=[384, 217, 100]),
))

# 10. Hotel Property Info
b10 = {"HotelIDs": "384"}
APIS.append(run_api(
    "Hotel Property Info",
    f"{BASE}/api/xconnect/gethotelstaticdatalistsuboptimize_v1_Address",
    "POST",
    hotel_static_headers,
    b10,
    lambda: call_hotel_property_info(hotel_ids=[384]),
))

# 11. Hotel Descriptions
b11 = {"HotelIDs": "384"}
APIS.append(run_api(
    "Hotel Descriptions",
    f"{BASE}/api/xconnect/GetPropertyDescriptions",
    "POST",
    hotel_static_headers,
    b11,
    lambda: call_hotel_descriptions(hotel_ids=[384]),
))

# 12. Hotel Guest Review
b12 = {"HotelID": 384}
APIS.append(run_api(
    "Hotel Guest Review",
    f"{BASE}/api/xconnect/GetHotelGuestReview",
    "POST",
    hotel_static_headers,
    b12,
    lambda: call_hotel_guest_review(hotel_id=384),
))

# 13. Hotel Availability
b13 = {
    "CheckIn": "08-03-2026", "CheckOut": "08-05-2026",
    "Nationality": "India",
    "Request": {"Rooms": [{"RoomNo": 1, "NoofAdults": 2, "NoOfChild": 0, "ChildAge": ""}],
                "CityID": "6", "CheckInDate": "08-03-2026", "CheckOutDate": "08-05-2026",
                "NoofNights": "2", "Nationality": "India",
                "Filters": {"IsRecommendedOnly": "0", "IsShowRooms": "1",
                            "IsOnlyAvailable": "1", "StarRating": {"Min": 1, "Max": 5},
                            "HotelIDs": "384,217"}},
    "AdvancedOptions": {"Currency": "AED", "CustomerIpAddress": "111", "HotelName": ""},
    "IsMobile": 1, "IsSearch": 1,
}
APIS.append(run_api(
    "Hotel Availability",
    f"{BASE}/api/xconnect/Availabilitywithcancellation",
    "POST",
    base_headers,
    b13,
    lambda: call_hotel_availability(
        hotel_ids=[384, 217], city_id=6,
        check_in="2026-08-03", check_out="2026-08-05", adults=2,
    ),
))

# 14. Visa Info
b14 = {
    "countryId": 213, "nationalityId": 101,
    "travelDate": "08-03-2026", "visaTypeId": 1,
    "guestInfo": {"adults": 1, "children": 0},
}
APIS.append(run_api(
    "Visa Info",
    f"{BASE}/api/visa/v1/visas",
    "POST",
    base_headers,
    b14,
    lambda: call_visa_info(country_id=213, nationality_id=101, travel_date="2026-08-03"),
))

# 15. Tour Search
b15 = {"countryId": 229, "cityId": 6, "travelDate": "08-03-2026", "tourCategoryId": 1}
APIS.append(run_api(
    "Tour Search",
    f"{BASE}/api/v1/tourservices/TourSearch/toursearchlist",
    "POST",
    base_headers,
    b15,
    lambda: call_tour_search(country_id=229, city_id=6, travel_date="2026-08-03"),
))

# 16. Restaurant Search
b16 = {"cityid": 6, "GuestInfo": {"Adults": 1, "Children": 0}, "SearchDate": "08-03-2026"}
APIS.append(run_api(
    "Restaurant Search",
    f"{BASE}/api/restaurant/v1/restaurants",
    "POST",
    base_headers,
    b16,
    lambda: call_restaurant_search(city_id=6, search_date="2026-08-03"),
))

# 17. Transfer Search
b17 = {
    "fromLat": 25.2532, "fromLng": 55.3657,
    "toLat": 25.2048, "toLng": 55.2708,
    "fromPlaceId": "DXB", "toPlaceId": "hotel_384",
    "departureDate": "08-03-2026", "departureTime": "07:00:00",
    "isRoundTrip": False, "fromType": "A", "toType": "P",
}
APIS.append(run_api(
    "Transfer Search",
    f"{BASE}/api/transferservices/TransferList",
    "POST",
    base_headers,
    b17,
    lambda: call_transfer_search(
        from_lat=25.2532, from_lng=55.3657,
        to_lat=25.2048, to_lng=55.2708,
        from_place_id="DXB", to_place_id="hotel_384",
        departure_date="2026-08-03",
    ),
))

# 18. Package List
b18 = {"countryId": 229, "cityId": 6, "adults": 2, "children": 0,
       "nationality": "India", "residency": "India"}
APIS.append(run_api(
    "Package List",
    f"{BASE}/api/staticpackageservices/staticpackage/packagelist",
    "POST",
    base_headers,
    b18,
    lambda: call_list_packages(country_id=229, city_id=6),
))

# 19. Flight Search
b19 = {
    "OriginDestinationInformations": [
        {"DepartureDateTime": "11-08-2026", "OriginLocationCode": "DEL", "DestinationLocationCode": "DXB"},
    ],
    "TravelPreferences": {"MaxStopsQuantity": "1", "CabinPreference": "Y", "AirTripType": "oneway"},
    "PricingSourceType": "all",
    "PassengerTypeQuantities": [{"Code": "ADT", "Quantity": 1}],
    "childAge": "", "infantAge": "", "Target": "test",
    "agentID": 0, "rateCategoryId": 0, "supplierTime": "1",
    "supplierId": 0, "suppliers": [], "isMobile": 0, "AirlineName": "",
}
APIS.append(run_api(
    "Flight Search",
    f"{BASE}/api/Flight/search",
    "POST",
    flight_search_headers,
    b19,
    lambda: call_flight_search(
        origin_iata="DEL", destination_iata="DXB",
        departure_date="2026-08-11", adults=1,
    ),
))

# ── write Excel ───────────────────────────────────────────────────────────────

print("\nWriting Excel...")

wb = openpyxl.Workbook()

# ── Sheet 1: Summary ──────────────────────────────────────────────────────────
ws_sum = wb.active
ws_sum.title = "Summary"

HDR_FILL   = PatternFill("solid", fgColor="1F4E79")
HDR_FONT   = Font(bold=True, color="FFFFFF", size=11)
OK_FILL    = PatternFill("solid", fgColor="C6EFCE")
FAIL_FILL  = PatternFill("solid", fgColor="FFC7CE")
SLOW_FILL  = PatternFill("solid", fgColor="FFEB9C")
ALT_FILL   = PatternFill("solid", fgColor="EBF3FB")
WRAP       = Alignment(wrap_text=True, vertical="top")
CENTER     = Alignment(horizontal="center", vertical="top")
thin       = Side(style="thin", color="CCCCCC")
BORDER     = Border(left=thin, right=thin, top=thin, bottom=thin)

summary_cols = [
    ("API Name",           30),
    ("Endpoint",           55),
    ("Method",             8),
    ("Response Time (ms)", 18),
    ("Response Size (KB)", 18),
    ("HTTP Status",        12),
    ("Success",            10),
    ("Error",              50),
    ("Full Response",      120),
]

for ci, (col, width) in enumerate(summary_cols, 1):
    cell = ws_sum.cell(row=1, column=ci, value=col)
    cell.font = HDR_FONT
    cell.fill = HDR_FILL
    cell.alignment = CENTER
    cell.border = BORDER
    ws_sum.column_dimensions[get_column_letter(ci)].width = width

ws_sum.row_dimensions[1].height = 22

for ri, row in enumerate(APIS, 2):
    values = [
        row["API Name"], row["Endpoint"], row["Method"],
        row["Response Time (ms)"], row["Response Size (KB)"],
        row["HTTP Status"], row["Success"],
        row["Error"], row["Full Response"],
    ]
    is_alt = (ri % 2 == 0)
    for ci, val in enumerate(values, 1):
        cell = ws_sum.cell(row=ri, column=ci, value=val)
        cell.alignment = WRAP
        cell.border = BORDER
        if ci == 7:  # Success column
            cell.fill = OK_FILL if val == "TRUE" else FAIL_FILL
        elif ci == 4 and isinstance(val, (int, float)):  # Time column
            cell.fill = FAIL_FILL if val > 10000 else (SLOW_FILL if val > 3000 else (OK_FILL if val > 0 else ALT_FILL))
        elif is_alt:
            cell.fill = ALT_FILL
    # row height based on response size — more lines = taller, cap at 409
    resp_lines = str(row["Full Response"]).count("\n") + 1
    ws_sum.row_dimensions[ri].height = min(max(18, resp_lines * 3), 409)

ws_sum.freeze_panes = "A2"
ws_sum.auto_filter.ref = f"A1:{get_column_letter(len(summary_cols))}1"

# ── Sheet 2+: One sheet per API with full details ─────────────────────────────
detail_cols = [
    ("Field",         22),
    ("Value",        120),
]

for row in APIS:
    safe_name = row["API Name"][:31].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "").replace("[", "").replace("]", "").replace(":", "-")
    ws = wb.create_sheet(title=safe_name)

    for ci, (col, width) in enumerate(detail_cols, 1):
        cell = ws.cell(row=1, column=ci, value=col)
        cell.font = HDR_FONT
        cell.fill = HDR_FILL
        cell.alignment = CENTER
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(ci)].width = width

    fields = [
        ("API Name",           row["API Name"]),
        ("Endpoint",           row["Endpoint"]),
        ("Method",             row["Method"]),
        ("Response Time (ms)", row["Response Time (ms)"]),
        ("Response Size (KB)", row["Response Size (KB)"]),
        ("HTTP Status",        row["HTTP Status"]),
        ("Success",            row["Success"]),
        ("Error",              row["Error"]),
        ("--- HEADERS ---",    ""),
        ("Headers Sent",       row["Headers"]),
        ("--- REQUEST ---",    ""),
        ("Request Body",       row["Request Body"]),
        ("--- RESPONSE ---",   ""),
        ("Full Response",      row["Full Response"]),
    ]

    for ri, (field, value) in enumerate(fields, 2):
        fc = ws.cell(row=ri, column=1, value=field)
        vc = ws.cell(row=ri, column=2, value=str(value) if value is not None else "")
        fc.font = Font(bold=True, size=10)
        fc.alignment = Alignment(vertical="top")
        vc.alignment = Alignment(wrap_text=True, vertical="top")
        fc.border = BORDER
        vc.border = BORDER
        if field.startswith("---"):
            fc.fill = PatternFill("solid", fgColor="BDD7EE")
            vc.fill = PatternFill("solid", fgColor="BDD7EE")

        # Auto height: ~15px per line, cap at 409 (Excel max)
        lines = str(value).count("\n") + 1 if value else 1
        ws.row_dimensions[ri].height = min(15 * lines, 409)

    ws.freeze_panes = "A2"

# ── Save ──────────────────────────────────────────────────────────────────────
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
fname = f"api_benchmark_{ts}.xlsx"
wb.save(fname)
print(f"\nSaved: {fname}")
print(f"Total APIs: {len(APIS)} | Sheets: 1 summary + {len(APIS)} detail sheets")
