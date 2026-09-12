"""Tour variants and their transfer prices, straight from the supplier."""

from __future__ import annotations

import concurrent.futures as cf
from typing import Any

from booking_api.endpoints import call_tour_option_rate, call_tour_options
from fx import convert_supplier_price


def _tiers(rate_row: dict[str, Any], adults: int) -> list[dict[str, Any]]:
    rows = rate_row.get("initialTransferRates") or []
    base = next(
        (r["totalTransferRate"] for r in rows if r.get("transferTypeId") == 3), None
    )
    if base is None:
        return []

    out = []
    for r in rows:
        tid = r.get("transferTypeId")
        if tid == 3 or r.get("totalTransferRate") is None:
            continue
        per_head = float(r["totalTransferRate"]) - float(base)
        aed = per_head * adults if tid == 1 else float(r.get("startingFromRate") or 0)
        if aed <= 0:
            continue
        inr, _ = convert_supplier_price(aed, fare_currency=r.get("currencyCode") or "AED")
        if inr:
            out.append({
                "transfer_type": r.get("transferTypeName"),
                "price_inr": inr,
                "price_display": f"₹{inr:,.0f}",
                "priced_for_pax": adults,
            })
    return out


def transfer_prices(rate_row: dict[str, Any], adults: int) -> list[dict[str, Any]]:
    """Transfer tiers as search_tours puts them on a tour row."""
    return _tiers(rate_row, adults)


def tour_image_url(tour_id: int, travel_date: str) -> str:
    """The tour's own image, for its variants to share.

    Variant rows carry no image of their own, so without this every variant
    card renders an empty placeholder. Cached at the static tier: a photo does
    not change with the date or the party size.
    """
    from booking_api.endpoints import call_tour_search
    from parsers import DEFAULT_IMAGE_BASE_URL, _resolve_image_url
    from reference_data_loader import resolve_city

    from .result_cache import _STATIC_TTL, cached_or_call

    def _produce() -> dict[str, Any]:
        city = resolve_city("Dubai") or {}
        if not city.get("city_id"):
            return {"images": {}}
        listed = (
            call_tour_search(
                country_id=int(city["country_id"]),
                city_id=int(city["city_id"]),
                travel_date=travel_date,
            )
            .get("result", {})
            .get("tourStaticlists")
            or []
        )
        images = {}
        for t in listed:
            tid = str(t.get("tourId") or t.get("tourID") or "")
            url = _resolve_image_url(t.get("imagePath"), DEFAULT_IMAGE_BASE_URL)
            if tid and url:
                images[tid] = url
        return {"images": images}

    try:
        got = cached_or_call(
            "tour_images", {"travel_date": travel_date}, _produce, ttl=_STATIC_TTL
        )
        return str((got.get("images") or {}).get(str(tour_id)) or "")
    except Exception:
        return ""


def _private_for_large_party(
    tour_id: int, option: dict[str, Any], travel_date: str, adults: int
) -> dict[str, Any] | None:
    """Private transfer for a party the supplier will not price in one booking.

    It returns HTTP 500 above its own ceiling (13+ on most tours), but 17
    people is a real booking -- it just needs more vehicles. The seat count is
    NOT maxPax: Desert Safari reports maxPax 12 while its price doubles at 7
    pax, so a car holds 6 and 12 is a two-car ceiling. Probing where the price
    actually steps gives the true capacity and the per-vehicle rate, and the
    quote is that rate times the number of cars needed.
    """
    one_car = seats = None
    for pax in range(1, 13):
        try:
            row = call_tour_option_rate(
                tour_id=tour_id,
                option_id=option["optionId"],
                supplier_id=option.get("supplierId") or 0,
                travel_date=travel_date,
                adults=pax,
                transfer_id=3,
            )["result"][0]
        except Exception:
            break
        rate = next(
            (
                float(t.get("startingFromRate") or 0)
                for t in row.get("initialTransferRates") or []
                if t.get("transferTypeId") == 2
            ),
            0.0,
        )
        if rate <= 0:
            break
        if one_car is None:
            one_car = rate
        elif rate > one_car:
            seats = pax - 1
            break
    if one_car is None:
        return None
    if seats is None:
        return None

    cars = -(-adults // seats)
    inr, _ = convert_supplier_price(one_car * cars, fare_currency="AED")
    if not inr:
        return None
    return {
        "transfer_type": "Private Transfer",
        "price_inr": inr,
        "price_display": f"₹{inr:,.0f}",
        "priced_for_pax": adults,
        "vehicles": cars,
        "seats_per_vehicle": seats,
    }


def tour_options(tour_id: int, travel_date: str, adults: int) -> dict[str, Any]:
    """Every bookable variant of one tour, priced for `adults` people."""
    listed = (
        call_tour_options(tour_id=tour_id, travel_date=travel_date)
        .get("result", {})
        .get("tourOptionlist")
        or []
    )

    def price(option: dict[str, Any]) -> dict[str, Any]:
        name = (option.get("optionName") or "").strip()
        row: dict[str, Any] = {
            "option_id": option.get("optionId"),
            "tour_id": tour_id,
            "supplier_id": option.get("supplierId"),
            "name": name,
            "is_addon": "add-on" in name.lower(),
        }
        try:
            rate = call_tour_option_rate(
                tour_id=tour_id,
                option_id=option["optionId"],
                supplier_id=option.get("supplierId") or 0,
                travel_date=travel_date,
                adults=adults,
                transfer_id=3,
            )["result"][0]
        except Exception:
            # Over the supplier's own booking ceiling. The ticket cannot be
            # priced in one booking, but the party still needs vehicles, so
            # quote the private transfer from the per-vehicle rate.
            row["price_display"] = "On request"
            big = _private_for_large_party(tour_id, option, travel_date, adults)
            if big:
                row["transfer_prices"] = [big]
            return row

        inr, _ = convert_supplier_price(
            rate.get("rate"), fare_currency=rate.get("currencyCode") or "AED"
        )
        if not inr:
            row["price_display"] = "On request"
            return row

        row["price_total_inr"] = inr
        row["price_per_adult_inr"] = round(inr / adults, 2)
        row["price_total_display"] = f"₹{inr:,.0f} total for {adults} adult(s)"
        row["transfer_prices"] = _tiers(rate, adults)

        # "Private Majlis - Per Group up to 6 Guests" is one majlis for the
        # party, not a per-head charge. The supplier still multiplies it by
        # pax, so we show what it bills and say the basis, rather than
        # dividing it down to a per-adult figure it will not honour.
        if "per group" in name.lower():
            row["price_basis"] = "per_group"
            row["price_display"] = f"₹{inr:,.0f} for {adults} guest(s) — priced per group"
            row["price_note"] = (
                "The supplier bills this group item once per guest in the "
                "party. Confirm the final charge at booking."
            )
        else:
            row["price_basis"] = "per_adult"
            row["price_display"] = f"₹{inr / adults:,.0f} per adult"
        return row

    with cf.ThreadPoolExecutor(max_workers=max(1, len(listed))) as ex:
        image = ex.submit(tour_image_url, tour_id, travel_date)
        options = list(ex.map(price, listed))

    # Variants share the tour's picture; the cards read it per row.
    url = image.result()
    if url:
        for o in options:
            o["image_url"] = url

    return {
        "tour_id": tour_id,
        "adults": adults,
        "image_url": url,
        "options": options,
        "note": (
            f"Ticket and transfer figures are TOTALS for {adults} adult(s). "
            "Quote them as they are; do not multiply."
        ),
    }
