"""Run every parser against the real API samples (api_samples.json).
Reports what works and what breaks per parser, so we can fix systematically.

Usage:
    # Default: look for api_samples.json at the repo root
    python -m scripts.validate_against_samples

    # Override:
    python -m scripts.validate_against_samples path/to/api_samples.json

If the file doesn't exist, run scripts/sample_all_apis.py first to generate it.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_SAMPLES_PATH = ROOT / "api_samples.json"


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def ok(msg: str) -> None:
    print(c(f"  ✓ {msg}", "32"))


def warn(msg: str) -> None:
    print(c(f"  ⚠ {msg}", "33"))


def fail(msg: str) -> None:
    print(c(f"  ✗ {msg}", "31"))


def header(name: str) -> None:
    print()
    print(c(f"━━━ {name} " + "━" * max(0, 60 - len(name)), "36"))


def main() -> int:
    samples_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLES_PATH
    if not samples_path.exists():
        fail(f"Samples file not found: {samples_path}")
        print()
        print("  Generate it first:")
        print("    python scripts/sample_all_apis.py")
        print()
        print("  Or pass a custom path:")
        print("    python -m scripts.validate_against_samples /path/to/api_samples.json")
        return 1

    with samples_path.open() as f:
        data = json.load(f)
    endpoints = data["endpoints"]

    # ----- Flight -----
    header("parse_flight_response")
    try:
        from parsers import parse_flight_response

        raw = endpoints["FlightSearch"]["response"]
        options = parse_flight_response(
            raw, expected_origin="DEL", expected_destination="BOM", max_results=5
        )
        if options:
            ok(
                f"Parsed {len(options)} options from {len(raw['data']['pricedItineraries'])} itineraries"
            )
            o = options[0]
            print(f"     cheapest: {o.airline} {o.price_inr:.0f} INR")
            print(f"     route: {o.route_outbound}")
            print(f"     stops: {o.stops}, refundable: {o.refundable}")
            print(f"     baggage: {o.baggage_info}, cabin_baggage: {o.cabin_baggage_info}")
            print(f"     segments_outbound: {len(o.segments_outbound)}")
        else:
            warn("Returned 0 options")
    except Exception as e:
        fail(f"EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ----- Hotel -----
    header("parse_hotel_response")
    try:
        from parsers import parse_hotel_response

        raw = endpoints["HotelSearch"]["response"]
        options = parse_hotel_response(
            raw,
            nights=2,
            hotel_names={"509": "Rove Downtown", "206": "Citymax Bur Dubai"},
            hotel_areas={"509": "Downtown", "206": "Bur Dubai"},
        )
        if options:
            ok(f"Parsed {len(options)} hotels")
            for h in options:
                print(
                    f"     {h.hotel_name}: ₹{h.price_inr:.0f} ({h.per_night_inr:.0f}/night, {h.stars} star)"
                )
                print(f"       cheapest: {h.cheapest_room_type} / {h.cheapest_board}")
                print(f"       free_cancel: {h.has_free_cancellation}")
        else:
            warn("Returned 0 options")
    except Exception as e:
        fail(f"EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ----- Tour -----
    header("parse_tour_response")
    try:
        from parsers import parse_tour_response

        list_raw = endpoints["TourList"]["response"]
        rate_raw = endpoints["TourListrate"]["response"]
        options = parse_tour_response(list_raw, rate_raw, max_results=5)
        if options:
            ok(
                f"Parsed {len(options)} tours (top 5 of "
                f"{len(list_raw['result']['tourStaticlists'])} list, "
                f"{len(rate_raw['result'])} rates)"
            )
            for t in options:
                print(f"     [{t.tour_id}] {t.name[:55]}: ₹{t.price_per_adult_inr:.0f}")
                print(f"       category={t.category} rating={t.rating} reviews={t.reviews_count}")
                print(f"       recommended={t.is_recommended}")
        else:
            warn("Returned 0 tours")
    except Exception as e:
        fail(f"EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ----- Transfer (empty result) -----
    header("parse_transfer_response (empty case)")
    try:
        from parsers import parse_transfer_response

        raw = endpoints["TransferList"]["response"]
        options = parse_transfer_response(raw, max_results=5)
        if not options:
            ok(f"Returned [] cleanly for empty result (body statusCode={raw.get('statusCode')})")
        else:
            ok(f"Parsed {len(options)} transfers")
    except Exception as e:
        fail(f"EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ----- Restaurant -----
    header("parse_restaurant_response")
    try:
        from parsers import parse_restaurant_response

        raw = endpoints["RestaurantList"]["response"]
        options = parse_restaurant_response(raw, max_results=5)
        if options:
            ok(f"Parsed {len(options)} restaurants from {len(raw['result']['list'])} listed")
            for r in options:
                print(f"     [{r.restaurant_id}] {r.name}")
                print(f"       cuisine={r.cuisine} veg_type={r.veg_type}")
                print(f"       price=₹{r.price_per_adult_inr:.0f} ({r.currency_original})")
                print(f"       hours: {r.opening_time}-{r.closing_time}")
        else:
            warn("Returned 0 restaurants")
    except Exception as e:
        fail(f"EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ----- Visa -----
    header("parse_visa_response")
    try:
        from parsers import parse_visa_response

        raw = endpoints["VisaList"]["response"]
        options = parse_visa_response(raw)
        if options:
            ok(f"Parsed {len(options)} visa options")
            for v in options:
                print(f"     [{v.visa_id}] {v.visa_type}")
                print(f"       validity={v.validity}, stay={v.stay_duration}")
                print(f"       processing={v.processing_days} days, evisa={v.is_evisa}")
                print(f"       pricing_available={v.pricing_available}")
        else:
            warn("Returned 0 visa options")
    except Exception as e:
        fail(f"EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ----- Package -----
    header("parse_package_response")
    try:
        from parsers import parse_package_response

        list_raw = endpoints["PackageList"]["response"]
        rate_raw = endpoints["PackageRate"]["response"]
        options = parse_package_response(list_raw, rate_raw, max_results=5)
        if options:
            ok(f"Parsed {len(options)} packages")
            for p in options[:3]:
                print(f"     [{p.get('package_id')}] {p.get('name')}")
                print(f"       price=₹{p.get('price_inr', 0):.0f}")
                print(f"       duration={p.get('duration')}")
        else:
            warn(
                "Returned 0 packages (rate_raw['result'] was "
                f"{type(rate_raw.get('result')).__name__})"
            )
    except Exception as e:
        fail(f"EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
