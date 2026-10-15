"""Build one realistic `ToolResult` fixture per kind.

Run:  python scripts/build_contract_fixtures.py

The fixtures are the shared ground truth for Phase 0: the web UI builds cards
against them before the retrieval layer exists, and both test suites load them.
They are BUILT THROUGH THE MODELS rather than hand-written as JSON, so a
fixture that does not satisfy the contract cannot be committed.

The figures are the real ones this supplier returns, kept deliberately: the
Desert Safari sharing tier really is one price per traveller while its private
tier is one price for a vehicle of up to six, and a fixture that smoothed that
away would let us build a card that cannot show the difference.
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts import (
    Badge,
    Degraded,
    ImageRef,
    MoreInfo,
    Row,
    Suggestion,
    ToolResult,
)

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "contract"

AS_OF = "2026-09-29T09:15:00Z"


def img(name: str, ratio: float) -> ImageRef:
    """Card images always point at OUR proxy, never the supplier's host."""
    return ImageRef(src=f"/api/img?src=supplier%2F{name}.jpg&w=640", alt="", ratio=ratio)


# ------------------------------------------------------------------- tours
def tours() -> ToolResult:
    return ToolResult(
        ref="ref_tours_dxb_1012",
        kind="tours",
        summary_md=(
            "Three of the 13 desert safaris running on 12 October. Prices are "
            "totals for 2 travellers.\n"
        ),
        rows=[
            Row(
                id="28488",
                kind="tours",
                title="Dubai Desert Safari with BBQ Dinner",
                subtitle="6 hours · Adventure",
                price_inr=1793.0,
                price_basis="per_adult",
                priced_for_pax=2,
                badges=[
                    Badge(label="Recommended", tone="ok"),
                    Badge(label="Free cancellation 24h", tone="ok"),
                    Badge(label="3 ticket options", tone="neutral"),
                ],
                image=img("tour-28488", 1.777),
                detail_ref="tours:28488",
                facets={
                    "Duration": "6 hours",
                    "Transfer": "Sharing ₹1,793 · Private ₹11,569 (vehicle, up to 6)",
                    "Cancellation": "Free until 24 hours before",
                    "Start times": "Flexible — no fixed start time",
                    "Add-ons": "2 add-ons: Quad Bike, Camel Ride",
                },
            ),
            Row(
                id="28502",
                kind="tours",
                title="Evening Desert Safari — Premium Camp",
                subtitle="7 hours · Adventure",
                price_inr=2640.0,
                price_basis="per_adult",
                priced_for_pax=2,
                badges=[Badge(label="Free cancellation 48h", tone="ok")],
                image=img("tour-28502", 1.777),
                detail_ref="tours:28502",
                facets={
                    "Duration": "7 hours",
                    "Transfer": "Sharing ₹2,640 · Private ₹13,900 (vehicle, up to 6)",
                    "Cancellation": "Free until 48 hours before",
                    "Start times": "Flexible — no fixed start time",
                },
            ),
            Row(
                id="30112",
                kind="tours",
                title="Burj Khalifa Level 124 & 125",
                subtitle="1 hour 30 minutes · Sightseeing",
                price_inr=4180.0,
                price_basis="per_adult",
                priced_for_pax=2,
                badges=[
                    Badge(label="33 start times", tone="neutral"),
                    Badge(label="Non-refundable", tone="warn"),
                ],
                image=img("tour-30112", 1.777),
                detail_ref="tours:30112",
                facets={
                    "Duration": "1 hour 30 minutes",
                    "Transfer": "Without transfer — no pickup on this ticket",
                    "Cancellation": "Non-refundable",
                    "Start times": "33 start times: 09:00, 09:30, 10:00, 10:30, 11:00, 11:30 …",
                },
            ),
        ],
        facts={
            "priced_for_pax": 2,
            "currency": "INR",
            "as_of": AS_OF,
            "travel_date": "2026-10-12",
            "destination": "Dubai",
            "total_available": 13,
        },
        more=MoreInfo(total_available=13, showing=3, next_offset=3),
        suggest=[
            Suggestion(
                kind="transfers",
                reason="adjacent",
                text="You have no airport transfer for the 12th yet.",
                action="search_transfers(from='DXB', to='Rove Downtown', date='2026-10-12')",
            ),
            Suggestion(
                kind="tours",
                reason="gap",
                text="Nothing is booked for the 14th.",
                action="search_tours(destination_city='Dubai', travel_date='2026-10-14')",
            ),
        ],
    )


# ------------------------------------------------------------------ hotels
def hotels() -> ToolResult:
    return ToolResult(
        ref="ref_hotels_dxb_1012",
        kind="hotels",
        summary_md="Three 4-star options in Downtown for 12–16 October, 1 room, 2 adults.\n",
        rows=[
            Row(
                id="1350",
                kind="hotels",
                title="Rove Downtown",
                subtitle="Downtown Dubai · 4 star",
                price_inr=15500.0,
                price_basis="per_room_night",
                priced_for_pax=2,
                badges=[
                    Badge(label="Refundable", tone="ok"),
                    Badge(label="Breakfast included", tone="neutral"),
                ],
                image=img("hotel-1350", 1.5),
                detail_ref="hotels:1350",
                facets={
                    "Board": "Bed and breakfast",
                    "Cancellation": "Free until 10 October",
                    "Rooms available": "6 of 9 rates are refundable",
                    "Total for 4 nights": "₹62,000",
                },
            ),
            Row(
                id="1412",
                kind="hotels",
                title="Millennium Place Barsha Heights",
                subtitle="Barsha Heights · 4 star",
                price_inr=11200.0,
                price_basis="per_room_night",
                priced_for_pax=2,
                badges=[Badge(label="Refundable", tone="ok")],
                image=img("hotel-1412", 1.5),
                detail_ref="hotels:1412",
                facets={
                    "Board": "Room only",
                    "Cancellation": "Free until 9 October",
                    "Rooms available": "3 of 7 rates are refundable",
                    "Total for 4 nights": "₹44,800",
                },
            ),
            Row(
                id="1508",
                kind="hotels",
                title="Address Dubai Mall",
                subtitle="Downtown Dubai · 5 star",
                price_inr=31900.0,
                price_basis="per_room_night",
                priced_for_pax=2,
                badges=[
                    Badge(label="Non-refundable", tone="warn"),
                    Badge(label="Burj view", tone="neutral"),
                ],
                image=img("hotel-1508", 1.5),
                detail_ref="hotels:1508",
                facets={
                    "Board": "Bed and breakfast",
                    "Cancellation": "No refundable rate at this hotel for these dates",
                    "Total for 4 nights": "₹1,27,600",
                },
            ),
        ],
        facts={
            "priced_for_pax": 2,
            "currency": "INR",
            "as_of": AS_OF,
            "check_in": "2026-10-12",
            "check_out": "2026-10-16",
            "nights": 4,
            "rooms": 1,
            "city_id": 115936,
        },
        more=MoreInfo(total_available=41, showing=3, next_offset=3),
    )


# ----------------------------------------------------------------- flights
def flights() -> ToolResult:
    return ToolResult(
        ref="ref_flights_bomdxb_1012",
        kind="flights",
        summary_md="Three return options, Mumbai to Dubai, 12–16 October, 2 adults.\n",
        rows=[
            Row(
                id="6E1471-6E1472",
                kind="flights",
                title="IndiGo 6E 1471",
                subtitle="BOM → DXB · non-stop",
                price_inr=38400.0,
                price_basis="total",
                priced_for_pax=2,
                badges=[
                    Badge(label="Non-stop", tone="ok"),
                    Badge(label="Cabin 7kg", tone="neutral"),
                ],
                detail_ref="flights:6E1471-6E1472",
                facets={
                    "Outbound": "12 Oct · 04:25 BOM → 06:00 DXB · 3h 05m",
                    "Return": "16 Oct · 07:10 DXB → 11:55 BOM · 3h 15m",
                    "Cabin": "Economy",
                    "Baggage": "7kg cabin, 20kg check-in",
                    "Fare type": "Saver — change fee applies",
                },
            ),
            Row(
                id="EK503-EK500",
                kind="flights",
                title="Emirates EK 503",
                subtitle="BOM → DXB · non-stop",
                price_inr=61250.0,
                price_basis="total",
                priced_for_pax=2,
                badges=[
                    Badge(label="Non-stop", tone="ok"),
                    Badge(label="30kg included", tone="ok"),
                ],
                detail_ref="flights:EK503-EK500",
                facets={
                    "Outbound": "12 Oct · 04:30 BOM → 06:05 DXB · 3h 05m",
                    "Return": "16 Oct · 10:15 DXB → 15:05 BOM · 3h 20m",
                    "Cabin": "Economy",
                    "Baggage": "7kg cabin, 30kg check-in",
                    "Fare type": "Flex — free date change",
                },
            ),
            Row(
                id="AI907-AI906",
                kind="flights",
                title="Air India AI 907",
                subtitle="BOM → DXB · 1 stop (DEL)",
                price_inr=34100.0,
                price_basis="total",
                priced_for_pax=2,
                badges=[
                    Badge(label="1 stop", tone="warn"),
                    Badge(label="Longest 9h 40m", tone="warn"),
                ],
                detail_ref="flights:AI907-AI906",
                facets={
                    "Outbound": "12 Oct · 06:00 BOM → 15:40 DXB · 9h 40m via DEL",
                    "Return": "16 Oct · 19:30 DXB → 06:20 BOM · 8h 20m via DEL",
                    "Cabin": "Economy",
                    "Baggage": "7kg cabin, 25kg check-in",
                    "Fare type": "Saver — non-refundable",
                },
            ),
        ],
        facts={
            "priced_for_pax": 2,
            "currency": "INR",
            "as_of": AS_OF,
            "origin": "BOM",
            "destination": "DXB",
            "departure_date": "2026-10-12",
            "return_date": "2026-10-16",
        },
        more=MoreInfo(total_available=27, showing=3, next_offset=3),
    )


# --------------------------------------------------------------- transfers
def transfers() -> ToolResult:
    return ToolResult(
        ref="ref_transfers_dxb_1012",
        kind="transfers",
        summary_md="Airport to Rove Downtown, 12 October, 2 travellers.\n",
        rows=[
            Row(
                id="TR-SED-4",
                kind="transfers",
                title="Private sedan",
                subtitle="DXB Terminal 3 → Rove Downtown",
                price_inr=2450.0,
                price_basis="per_vehicle",
                priced_for_pax=2,
                badges=[Badge(label="Private", tone="ok"), Badge(label="Meet and greet", tone="neutral")],
                image=img("transfer-sedan", 1.6),
                detail_ref="transfers:TR-SED-4",
                facets={
                    "Vehicle": "Sedan — up to 3 passengers",
                    "Luggage": "2 large bags",
                    "Type": "Private — your vehicle only",
                    "Waiting time": "60 minutes free after landing",
                },
            ),
            Row(
                id="TR-SHR-1",
                kind="transfers",
                title="Shared shuttle",
                subtitle="DXB Terminal 3 → Rove Downtown",
                price_inr=690.0,
                price_basis="per_adult",
                priced_for_pax=2,
                badges=[Badge(label="Shared", tone="warn")],
                image=img("transfer-shuttle", 1.6),
                detail_ref="transfers:TR-SHR-1",
                facets={
                    "Vehicle": "Shuttle bus — shared with other travellers",
                    "Luggage": "1 large bag each",
                    "Type": "Shared — may stop at other hotels",
                    "Waiting time": "Departs on the half hour",
                },
            ),
        ],
        facts={
            "priced_for_pax": 2,
            "currency": "INR",
            "as_of": AS_OF,
            "date": "2026-10-12",
            "pricing_note": (
                "Airport transfers are priced by distance and vehicle. This is "
                "not how tour transfers work — those are priced by published tier."
            ),
        },
    )


# ------------------------------------------------------------- restaurants
def restaurants() -> ToolResult:
    return ToolResult(
        ref="ref_restaurants_dxb_1013",
        kind="restaurants",
        summary_md="Two vegetarian-friendly options near Downtown for 13 October.\n",
        rows=[
            Row(
                id="RST-4412",
                kind="restaurants",
                title="Rangoli",
                subtitle="Indian · Downtown",
                price_inr=3200.0,
                price_basis="per_adult",
                priced_for_pax=2,
                badges=[Badge(label="4.4 rating", tone="ok"), Badge(label="Pure veg", tone="ok")],
                image=img("restaurant-4412", 1.5),
                detail_ref="restaurants:RST-4412",
                facets={
                    "Cuisine": "North Indian, Jain options",
                    "Timings": "12:00–15:30, 19:00–23:30",
                    "Set menu": "₹3,200 per adult",
                },
            ),
            Row(
                id="RST-4471",
                kind="restaurants",
                title="Asha's",
                subtitle="Indian · Wafi",
                price_inr=4100.0,
                price_basis="per_adult",
                priced_for_pax=2,
                badges=[Badge(label="4.2 rating", tone="ok")],
                image=img("restaurant-4471", 1.5),
                detail_ref="restaurants:RST-4471",
                facets={
                    "Cuisine": "Indian contemporary",
                    "Timings": "12:30–16:00, 19:00–00:00",
                    "Set menu": "₹4,100 per adult",
                },
            ),
        ],
        facts={"priced_for_pax": 2, "currency": "INR", "as_of": AS_OF, "date": "2026-10-13"},
    )


# -------------------------------------------------------------------- visa
def visa() -> ToolResult:
    return ToolResult(
        ref="ref_visa_uae_in",
        kind="visa",
        summary_md="UAE visa options for Indian passport holders travelling 12 October.\n",
        rows=[
            Row(
                id="VIS-30S",
                kind="visa",
                title="UAE Tourist Visa — 30 days, single entry",
                subtitle="Processing 3–4 working days",
                price_inr=8150.0,
                price_basis="per_adult",
                priced_for_pax=2,
                badges=[Badge(label="Most chosen", tone="ok")],
                detail_ref="visa:VIS-30S",
                facets={
                    "Validity": "60 days from issue",
                    "Stay": "30 days",
                    "Entries": "Single",
                    "Processing": "3–4 working days",
                },
            ),
            Row(
                id="VIS-60M",
                kind="visa",
                title="UAE Tourist Visa — 60 days, multiple entry",
                subtitle="Processing 4–5 working days",
                price_inr=19400.0,
                price_basis="per_adult",
                priced_for_pax=2,
                badges=[],
                detail_ref="visa:VIS-60M",
                facets={
                    "Validity": "60 days from issue",
                    "Stay": "60 days",
                    "Entries": "Multiple",
                    "Processing": "4–5 working days",
                },
            ),
        ],
        facts={
            "priced_for_pax": 2,
            "currency": "INR",
            "as_of": AS_OF,
            "nationality_id": 245,
            "nationality": "India",
        },
    )


# ---------------------------------------------------------------- packages
def packages() -> ToolResult:
    """A degraded result, on purpose.

    Every kind needs a fixture showing what the card deck does when the data is
    not fresh live inventory -- otherwise that path is built last and tested
    never, and the customer is the one who discovers it.
    """
    return ToolResult(
        ref="ref_packages_dxb_1012",
        kind="packages",
        summary_md=(
            "Two Dubai packages for 12–16 October. These prices were fetched "
            "4 minutes ago and can move; I will re-check before quoting a total.\n"
        ),
        rows=[
            Row(
                id="PKG-881",
                kind="packages",
                title="Dubai Highlights — 4 nights",
                subtitle="Hotel, transfers and 3 tours",
                price_inr=118500.0,
                price_basis="total",
                priced_for_pax=2,
                badges=[Badge(label="Cheaper than booking separately", tone="ok")],
                image=img("package-881", 1.777),
                detail_ref="packages:PKG-881",
                facets={
                    "Includes": "4 nights 4-star, airport transfers, 3 tours",
                    "Hotel": "Rove Downtown or similar",
                    "Cancellation": "Free until 5 October",
                },
            ),
            Row(
                id="PKG-903",
                kind="packages",
                title="Dubai Luxury — 4 nights",
                subtitle="5-star hotel, private transfers, 4 tours",
                price_inr=241000.0,
                price_basis="total",
                priced_for_pax=2,
                badges=[Badge(label="Private transfers", tone="neutral")],
                image=img("package-903", 1.777),
                detail_ref="packages:PKG-903",
                facets={
                    "Includes": "4 nights 5-star, private transfers, 4 tours",
                    "Hotel": "Address Dubai Mall",
                    "Cancellation": "Free until 1 October",
                },
            ),
        ],
        facts={"priced_for_pax": 2, "currency": "INR", "as_of": AS_OF, "nights": 4},
        degraded=Degraded(
            reason="cached",
            stale_seconds=247,
            disclosure=(
                "These package prices were fetched 4 minutes ago. I will pull "
                "fresh rates before quoting a final total."
            ),
        ),
        suggest=[
            Suggestion(
                kind="hotels",
                reason="bundle",
                text="The Highlights package works out ₹14,000 below the same pieces booked separately.",
                action="get_detail(kind='packages', item_id='PKG-881')",
            )
        ],
    )


BUILDERS = {
    "tours": tours,
    "hotels": hotels,
    "flights": flights,
    "transfers": transfers,
    "restaurants": restaurants,
    "visa": visa,
    "packages": packages,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in BUILDERS.items():
        result = build()
        path = OUT / f"{name}.json"
        path.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path.relative_to(OUT.parent.parent)}  ({len(result.rows)} rows)")


if __name__ == "__main__":
    main()
