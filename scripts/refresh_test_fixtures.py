"""Refresh tests/fixtures/*.json from a real api_samples.json file.

Reads /mnt/user-data/uploads/api_samples.json and writes trimmed fixtures
for each parser test. Keeps just enough data to exercise the parser
(2-3 items per endpoint) so tests stay fast.

Run after the client refreshes the staging API or whenever response
shapes drift:

    python -m scripts.refresh_test_fixtures
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = Path("/mnt/user-data/uploads/api_samples.json")
FIXTURES_DIR = ROOT / "tests" / "fixtures"


def trim_flight(raw: dict) -> dict:
    """Keep 4 itineraries covering the key parser branches:
    1. INR-priced ₹270 DEL→BOM (bogus, tests FLIGHT_PRICE_INR_FLOOR filter)
    2. USD-priced DEL→BOM, nonrefundable (tests normal happy path + currency)
    3. USD-priced DEL→BOM, refundable (tests refundable flag parsing)
    4. Any other destination (tests expected_destination filter)
    """
    itins = raw.get("data", {}).get("pricedItineraries", [])
    inr_low = next(
        (
            it
            for it in itins
            if it["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"].get("currencyCode")
            == "INR"
            and it["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"].get("amount", 0) < 500
        ),
        None,
    )
    usd_nonrefundable = next(
        (
            it
            for it in itins
            if it["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"].get("currencyCode")
            == "USD"
            and 100
            <= it["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"].get("amount", 0)
            <= 400
            and (it["airItineraryPricingInfo"].get("isRefundable", "") or "").lower()
            == "nonrefundable"
            and it["originDestinationOptions"][0]["flightSegments"][-1][
                "arrivalAirportLocationCode"
            ]
            == "BOM"
        ),
        None,
    )
    usd_refundable = next(
        (
            it
            for it in itins
            if it["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"].get("currencyCode")
            == "USD"
            and (it["airItineraryPricingInfo"].get("isRefundable", "") or "").lower()
            == "refundable"
            and it["originDestinationOptions"][0]["flightSegments"][-1][
                "arrivalAirportLocationCode"
            ]
            == "BOM"
        ),
        None,
    )
    wrong_dest = next(
        (
            it
            for it in itins
            if it["originDestinationOptions"][0]["flightSegments"][-1].get(
                "arrivalAirportLocationCode"
            )
            not in ("BOM", "DXB")
        ),
        None,
    )

    kept = [x for x in [inr_low, usd_nonrefundable, usd_refundable, wrong_dest] if x is not None]
    return {
        "id": None,
        "success": True,
        "data": {"pricedItineraries": kept},
        "error": [],
    }


def trim_hotel(raw: dict) -> dict:
    """Keep both hotels but only first 2 HotelOptions each (with first room of each)."""
    rs = raw["AvailabilityRS"]
    out_hotels = []
    for h in rs.get("HotelResult", []):
        opts = h.get("HotelOption", [])[:2]
        trimmed_opts = []
        for o in opts:
            rooms = o.get("HotelRooms", [])[:2]
            trimmed_opts.append({**o, "HotelRooms": rooms})
        out_hotels.append({**h, "HotelOption": trimmed_opts})
    return {
        "AvailabilityRS": {**rs, "HotelResult": out_hotels},
        "Error": raw.get("Error", []),
    }


def trim_tour_list(raw: dict) -> dict:
    """Keep tours that have a matching rate, plus one without (to test filter)."""
    return raw  # Filled in trim_tours_paired below


def trim_tour_rates(raw: dict) -> dict:
    return raw  # Filled in trim_tours_paired below


def trim_tours_paired(list_raw: dict, rate_raw: dict) -> tuple[dict, dict]:
    """Trim list + rates together so they overlap meaningfully.

    Keeps 4 priced tours (with matching rates) + 1 unpriced (for filter test).
    """
    rates_by_id = {r["tourID"]: r for r in rate_raw["result"] if isinstance(r, dict)}
    list_items = list_raw["result"]["tourStaticlists"]

    priced: list[dict] = []
    unpriced: list[dict] = []
    for t in list_items:
        tid = t.get("tourID")
        if tid in rates_by_id and len(priced) < 4:
            priced.append(t)
        elif tid not in rates_by_id and len(unpriced) < 1:
            unpriced.append(t)
        if len(priced) >= 4 and len(unpriced) >= 1:
            break

    kept_tours = priced + unpriced
    kept_rates = [rates_by_id[t["tourID"]] for t in priced]

    list_out = {
        "statusCode": 200,
        "error": None,
        "result": {"tourStaticlists": kept_tours, "operationTimes": [], "supplierlist": []},
    }
    rate_out = {"statusCode": 200, "error": None, "result": kept_rates}
    return list_out, rate_out


def trim_restaurant(raw: dict) -> dict:
    """Keep first 3 restaurants."""
    res = raw["result"]
    return {
        "statusCode": 200,
        "error": None,
        "result": {
            "list": res["list"][:3],
            "filters": res.get("filters", {}),
            "totalServices": min(3, res.get("totalServices", 0)),
            "slug": None,
        },
    }


def trim_visa(raw: dict) -> dict:
    """Keep first visa with all 4 options, drop heavy nested arrays we don't parse."""
    visas = raw["result"]["visas"]
    if not visas:
        return raw
    v = visas[0]
    # Keep only the fields the parser actually reads, plus a small slice of options
    slim_options = []
    for opt in v.get("options", [])[:4]:
        slim_options.append(
            {
                "visaOptionId": opt.get("visaOptionId"),
                "visaOptionName": opt.get("visaOptionName"),
                "processingTime": opt.get("processingTime"),
                "entryType": opt.get("entryType"),
                "validityPeriod": opt.get("validityPeriod"),
                "stayPeriod": opt.get("stayPeriod"),
                "isEvisa": opt.get("isEvisa"),
                "visaRates": opt.get("visaRates", []),
                "requiredDocuments": [
                    {"applicantType": d.get("applicantType"), "isRequired": d.get("isRequired")}
                    for d in (opt.get("requiredDocuments") or [])[:3]
                ],
            }
        )
    return {
        "statusCode": 200,
        "error": None,
        "result": {
            "visas": [
                {
                    "visaId": v.get("visaId"),
                    "name": v.get("name"),
                    "visaType": v.get("visaType"),
                    "visaTypeId": v.get("visaTypeId"),
                    "countryName": v.get("countryName"),
                    "options": slim_options,
                }
            ]
        },
    }


def trim_transfer(raw: dict) -> dict:
    """Keep as-is — already tiny (empty result)."""
    return raw


def main() -> int:
    if not SAMPLES_PATH.exists():
        print(f"Samples file not found: {SAMPLES_PATH}", file=sys.stderr)
        return 1

    with SAMPLES_PATH.open() as f:
        samples = json.load(f)
    endpoints = samples["endpoints"]

    tour_list_trimmed, tour_rate_trimmed = trim_tours_paired(
        endpoints["TourList"]["response"], endpoints["TourListrate"]["response"]
    )

    fixtures = {
        "flight_response.json": trim_flight(endpoints["FlightSearch"]["response"]),
        "hotel_response.json": trim_hotel(endpoints["HotelSearch"]["response"]),
        "tour_list_response.json": tour_list_trimmed,
        "tour_rate_response.json": tour_rate_trimmed,
        "restaurant_response.json": trim_restaurant(endpoints["RestaurantList"]["response"]),
        "visa_response.json": trim_visa(endpoints["VisaList"]["response"]),
        "transfer_response.json": trim_transfer(endpoints["TransferList"]["response"]),
    }

    FIXTURES_DIR.mkdir(exist_ok=True)
    for filename, data in fixtures.items():
        path = FIXTURES_DIR / filename
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        size = path.stat().st_size
        print(f"  ✓ {filename}: {size:,} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
