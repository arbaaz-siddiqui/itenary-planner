"""All countries: cities, airports, transfers, visas and plans beyond Dubai.

Was: cities.json gave a supplier id to Dubai only, so every other destination
was refused with "Dubai only, offer the Dubai equivalent", although the
supplier sells worldwide (575 priced tours in Singapore).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

LONDON_ROWS = {"data": [
    {"type": "city", "name_text": "London", "cityId": 17745, "countryId": 215, "city": "London",
     "country": "United Kingdom", "latitude": 51.50746, "longitude": -0.127673},
    {"type": "province_state", "name_text": "London Borough of Bexley", "cityId": 6655, "countryId": 215},
], "error": None}
DESERT_ROWS = {"data": [
    {"type": "city", "name_text": "Palm Desert", "cityId": 5244, "countryId": 227, "city": "Palm Desert",
     "country": "United States of America", "latitude": 33.72, "longitude": -116.38},
], "error": None}
BALI_TOUR_ROWS = {"data": [
    {"type": "City", "name_text": "Bali", "cityId": 314293, "countryId": 153, "city": "Bali",
     "country": "Indonesia", "latitude": -8.340539, "longitude": 115.09195},
], "error": None}
VISA_TABLE = {"result": [
    {"countryId": 213, "countryName": "United Arab Emirates", "countryCode": "AE"},
    {"countryId": 115, "countryName": "Singapore", "countryCode": "SG"},
    {"countryId": 87, "countryName": "Oman", "countryCode": "OM"},
    {"countryId": 133, "countryName": "Thailand", "countryCode": "TH"},
]}


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITY_SUPPLIER_LOOKUP", "1")
    from mcp_tools import get_visa_info

    get_visa_info._VISA_TABLE.update(at=0.0, rows=[])


def _entity(table: dict[tuple[str, str], dict[str, Any]]):
    calls: list[tuple[str, str]] = []

    def fake(*, service: str, query: str, size: int = 10) -> dict[str, Any]:
        calls.append((service, query.lower()))
        return table.get((service, query.lower()), {"data": [], "error": None})

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


# --- city resolution ---------------------------------------------------------

class TestSupplierCityFallback:
    def test_a_city_outside_cities_json_resolves_with_supplier_ids(self, online) -> None:
        from reference_data_loader import resolve_city

        fake = _entity({("hotels", "london"): LONDON_ROWS})
        with patch("booking_api.endpoints.call_entity_search", fake):
            city = resolve_city("London")
        assert city["city_id"] == 17745 and city["country_id"] == 215
        assert city["country_name"] == "United Kingdom" and city["iata"] == "LHR"

    def test_a_tour_only_city_is_found_through_the_tours_list(self, online) -> None:
        from reference_data_loader import resolve_city

        fake = _entity({("tours", "bali"): BALI_TOUR_ROWS})
        with patch("booking_api.endpoints.call_entity_search", fake):
            city = resolve_city("Bali")
        assert city["city_id"] == 314293
        # No airport is named Bali: the nearest large one is Denpasar.
        assert city["iata"] == "DPS"

    def test_ordinary_words_never_become_cities_even_online(self, online) -> None:
        from reference_data_loader import resolve_city

        fake = _entity({("hotels", "desert"): DESERT_ROWS})
        with patch("booking_api.endpoints.call_entity_search", fake):
            assert resolve_city("desert") is None

    def test_dubai_stays_offline(self, online) -> None:
        from reference_data_loader import resolve_city

        fake = _entity({})
        with patch("booking_api.endpoints.call_entity_search", fake):
            assert resolve_city("Dubai")["city_id"] == 244520
        assert fake.calls == []

    def test_offline_mode_makes_no_call(self) -> None:
        from reference_data_loader import resolve_city

        fake = _entity({("hotels", "london"): LONDON_ROWS})
        with patch("booking_api.endpoints.call_entity_search", fake):
            assert resolve_city("London") is None
        assert fake.calls == []

    def test_a_failed_lookup_is_not_cached(self, online) -> None:
        from reference_data_loader import resolve_city

        with patch("booking_api.endpoints.call_entity_search",
                   lambda **k: {"data": [], "error": "timeout"}):
            assert resolve_city("London") is None
        with patch("booking_api.endpoints.call_entity_search",
                   _entity({("hotels", "london"): LONDON_ROWS})):
            assert resolve_city("London")["city_id"] == 17745

    def test_an_indian_origin_gets_its_supplier_id(self, online) -> None:
        from reference_data_loader import resolve_city

        rows = {"data": [{"type": "city", "name_text": "Mumbai", "cityId": 271805, "countryId": 245,
                          "country": "India", "latitude": 19.07, "longitude": 72.87}], "error": None}
        with patch("booking_api.endpoints.call_entity_search", _entity({("hotels", "mumbai"): rows})):
            city = resolve_city("Mumbai")
        assert city["city_id"] == 271805 and city["iata"] == "BOM"

    def test_scanning_words_offline_never_calls(self, online) -> None:
        from reference_data_loader import resolve_city

        fake = _entity({("hotels", "london"): LONDON_ROWS})
        with patch("booking_api.endpoints.call_entity_search", fake):
            assert resolve_city("London", supplier=False) is None
        assert fake.calls == []


# --- airports ------------------------------------------------------------------

class TestAirports:
    @pytest.mark.parametrize("name,code", [
        ("London", "LHR"), ("Bangkok", "BKK"), ("Singapore", "SIN"), ("Paris", "CDG"),
        ("Dubai", "DXB"), ("Ahmedabad", "AMD"), ("Mumbai", "BOM"), ("Ahemdabad", "AMD"),
        ("Bali", "DPS"), ("Maldives", "MLE"),
    ])
    def test_a_city_resolves_to_its_main_airport(self, name: str, code: str) -> None:
        from reference_data_loader import resolve_iata

        assert resolve_iata(name) == code

    def test_a_raw_airport_code_is_accepted(self) -> None:
        from reference_data_loader import resolve_iata

        assert resolve_iata("LGW") == "LGW"
        assert resolve_iata("dmk") == "DMK"
        assert resolve_iata("ZZQ") is None

    def test_the_thirteen_indian_cities_keep_their_codes(self) -> None:
        from reference_data_loader import list_indian_origins, resolve_iata

        assert len(list_indian_origins()) == 13
        assert all(resolve_iata(c) for c in list_indian_origins())

    def test_nearest_airport_by_position(self) -> None:
        from reference_data_loader import city_airport

        # Kuta, Bali: no airport is named after the place we were given.
        assert city_airport("Nowhere Town", lat=-8.72, lng=115.17)["iata"] == "DPS"
        assert city_airport("Nowhere Town") is None

    def test_airport_record_shape(self) -> None:
        from reference_data_loader import airport

        rec = airport("SIN")
        assert rec["country"] == "Singapore" and rec["size"] == "large"
        assert rec["lat"] and rec["lng"]


# --- tools refuse honestly ---------------------------------------------------------

class TestRefusals:
    def test_tours_in_an_unknown_city_say_so_without_dubai(self) -> None:
        from mcp_tools.search_tours import _impl

        out = _impl(destination_city="Atlantis City", travel_date="2026-12-10", adults=2)
        assert out["error"] and out["error_type"] == "UnsupportedRoute"
        text = (out["message"] + out["agent_instructions"]).lower()
        assert "dubai" not in text and "could not find" in text

    def test_packages_in_a_city_without_supplier_id_say_nothing_sold(self) -> None:
        from mcp_tools.list_packages import _impl

        out = _impl(destination_city="Goa", check_in="2026-12-10", check_out="2026-12-13")
        assert out["error"] and "no holiday packages in Goa" in out["message"]
        assert "dubai" not in out["agent_instructions"].lower()

    def test_hotels_in_an_unknown_city(self) -> None:
        from mcp_tools.search_hotels import _impl

        out = _impl(destination_city="Atlantis City", check_in="2026-12-10",
                    check_out="2026-12-13", adults=2)
        assert out["error"] and "could not find" in out["message"]

    def test_hotel_area_outside_dubai_matches_the_address(self) -> None:
        from mcp_tools.search_hotels import _address_names_area

        assert _address_names_area("Sukhumvit", "123 Sukhumvit Soi 11, Bangkok")
        assert _address_names_area("covent garden", "1 Long Acre, Covent Garden, London")
        assert not _address_names_area("Silom", "123 Sukhumvit Soi 11, Bangkok")

    def test_a_named_hotel_is_searched_with_the_resolved_city(self) -> None:
        from mcp_tools.search_hotels import _resolve_hotel_by_name

        seen: list[str] = []

        def fake(*, service: str, query: str, size: int = 10) -> dict[str, Any]:
            seen.append(query)
            return {"data": [{"location_id": 9, "type": "hotel", "cityId": 70926,
                              "name_text": "Marina Bay Sands"}]}

        with patch("booking_api.call_entity_search", fake):
            assert _resolve_hotel_by_name("Marina Bay Sands", 70926, "Singapore") == 9
        assert seen[0] == "Marina Bay Sands Singapore"


# --- transfers ---------------------------------------------------------------------

class TestTransfers:
    def test_the_tool_is_renamed(self) -> None:
        from mcp_tools.search_transfers import search_transfers_tool

        assert search_transfers_tool.name == "search_airport_transfer"
        assert "destination_city" in search_transfers_tool.args

    def test_the_old_name_still_reaches_the_tool(self) -> None:
        from agent import _RENAMED_TOOLS

        assert _RENAMED_TOOLS["search_airport_transfer_dubai"] == "search_airport_transfer"

    def _run(self, city: str) -> dict[str, Any]:
        from mcp_tools.search_transfers import _impl

        sent: dict[str, Any] = {}
        hotel = {"data": [{"type": "hotel", "name_text": "Hotel X", "latitude": 1.28,
                           "longitude": 103.85, "cityId": 70926}]}
        with patch("booking_api.call_entity_search", lambda **k: hotel), \
             patch("mcp_tools.search_transfers.call_transfer_search",
                   lambda **k: sent.update(k) or {}), \
             patch("mcp_tools.search_transfers.resolve_city",
                   lambda n: {"name": city, "city_id": 1, "lat": None, "lng": None}):
            _impl(arrival_date="2026-12-10", destination_city=city, hotel_name="Hotel X")
        return sent

    def test_dubai_uses_its_configured_airport(self) -> None:
        from reference_data_loader import get_default_dubai_airport

        sent = self._run("Dubai")
        dxb = get_default_dubai_airport()
        assert sent["from_place_id"] == dxb["place_id"]
        assert sent["from_location_name"] == dxb["name"]

    def test_another_city_starts_at_its_own_airport(self) -> None:
        sent = self._run("Singapore")
        assert "Changi" in sent["from_location_name"] and sent["from_location_name"].endswith("(sin)")
        assert sent["from_place_id"] == ""
        assert round(sent["from_lat"], 1) == 1.4


# --- visa --------------------------------------------------------------------------

class TestVisaCountry:
    def _resolve(self, name: str):
        from mcp_tools.get_visa_info import resolve_visa_country

        with patch("booking_api.call_visa_countries", return_value=VISA_TABLE), \
             patch("booking_api.endpoints.call_entity_search", _entity({})):
            return resolve_visa_country(name)

    def test_countries_and_cities_resolve_to_the_suppliers_ids(self, online) -> None:
        row, name = self._resolve("Singapore")
        assert row["countryId"] == 115 and name == "Singapore"  # cities.json said 191
        row, name = self._resolve("UAE")
        assert row["countryId"] == 213 and name == "UAE"
        row, name = self._resolve("Dubai")
        assert row["countryId"] == 213
        row, name = self._resolve("TH")
        assert row["countryId"] == 133

    def test_a_country_the_supplier_does_not_sell_is_named(self, online) -> None:
        row, name = self._resolve("Japan")
        assert row is None and name == "Japan"

    def test_the_tool_answers_unsupported_plainly(self, online) -> None:
        from mcp_tools.get_visa_info import _impl

        with patch("booking_api.call_visa_countries", return_value=VISA_TABLE), \
             patch("booking_api.endpoints.call_entity_search", _entity({})):
            out = _impl(destination_country="Japan", nationality_country="India",
                        travel_date="2026-12-10")
        assert out["error"] and out["message"] == "We do not sell visas for Japan."
        assert "Singapore" in out["agent_instructions"]

    def test_the_tool_prices_with_the_visa_tables_id(self, online) -> None:
        from mcp_tools.get_visa_info import _impl

        seen: dict[str, Any] = {}
        with patch("booking_api.call_visa_countries", return_value=VISA_TABLE), \
             patch("mcp_tools.get_visa_info.call_visa_info", lambda **k: seen.update(k) or {}), \
             patch("mcp_tools.get_visa_info.parse_visa_response", return_value=[]):
            out = _impl(destination_country="Singapore", nationality_country="India",
                        travel_date="2026-12-10")
        assert seen["country_id"] == 115 and out["country"] == "Singapore"

    def test_offline_the_uae_still_resolves(self) -> None:
        from mcp_tools.get_visa_info import resolve_visa_country

        row, name = resolve_visa_country("Dubai")
        assert row["countryId"] == 213 and name == "UAE"

    def test_timeline_wording_names_the_country(self) -> None:
        from datetime import date

        from mcp_tools.get_visa_info import visa_timeline

        out = visa_timeline("2026-12-10", track_times={"Normal": "7-8 Working Days"}, weekend_days=[],
                            holidays=[], today=date(2026, 11, 17), country="Singapore",
                            country_code="SG")
        assert "(Singapore weekends excluded)" in out["note"]
        assert "decided by Singapore immigration" in out["note"]
        assert "UAE" not in out["note"]

    def test_a_fri_sat_country_skips_friday(self) -> None:
        from datetime import date

        from mcp_tools.get_visa_info import visa_timeline

        # Tue 17 Nov -> Tue 24 Nov: Oman works Sun-Thu, so 4 working days
        # (Wed, Thu, Sun, Mon); a Sat-Sun country gets 4 too but different ones.
        oman = visa_timeline("2026-11-24", track_times={"Normal": "1 Working Day"}, weekend_days=[],
                             holidays=[], today=date(2026, 11, 17), country="Oman", country_code="OM")
        uae = visa_timeline("2026-11-21", track_times={"Normal": "1 Working Day"}, weekend_days=[],
                            holidays=[], today=date(2026, 11, 17))
        assert oman["working_days"] == 4
        # 18, 19, 20 Nov (Wed-Fri): Friday counts in the UAE, not in Oman.
        assert uae["working_days"] == 3


# --- plan --------------------------------------------------------------------------

class TestPlanUsesTheDestination:
    def test_arrival_names_the_city(self) -> None:
        from scheduling import build_itinerary

        out = build_itinerary(start_date="2026-12-10", nights=3, arrival_time="10:00",
                              destination="Singapore")
        titles = [i["title"] for i in out["days"][0]["items"]]
        assert "Arrive in Singapore" in titles

    def test_catalogue_hotel_and_visa_are_for_the_destination(self) -> None:
        from agent_tools import plan_itinerary_tool

        cities: list[str] = []
        visa_asked: list[str] = []

        def index(travel_date: str, city: str = "Dubai"):
            cities.append(city)
            return ()

        def visa(**k: Any) -> dict[str, Any]:
            visa_asked.append(k["destination_country"])
            return {"country": "Singapore", "options": [{"price_per_person_inr": 5000.0}]}

        def hotels(**k: Any) -> dict[str, Any]:
            cities.append(k["destination_city"])
            return {"options": [{"hotel_name": "Hotel X", "price_inr": 40000.0}]}

        with patch("agent_tools._tour_facts_index", index), \
             patch("agent_tools._tour_slots", return_value=[]), \
             patch("agent_tools._catalogue", return_value={}), \
             patch("mcp_tools.search_hotels._impl", hotels), \
             patch("mcp_tools.get_visa_info._impl", visa), \
             patch("mcp_tools.quote_registry.was_quoted", return_value=True):
            out = plan_itinerary_tool.invoke({
                "start_date": "2026-12-10", "nights": 3, "adults": 2, "fill_days": True,
                "destination_city": "Singapore",
            })
        assert out["destination"] == "Singapore"
        assert set(cities) == {"Singapore"}
        assert visa_asked == ["Singapore"]
        items = [line["item"] for line in out["package_lines"]]
        assert "Singapore tourist visa" in items and "UAE tourist visa" not in items

    def test_a_pdf_without_a_destination_takes_the_plans(self, tmp_path, monkeypatch) -> None:
        import agent_tools

        monkeypatch.setenv("ITINERARY_DIR", str(tmp_path))
        from settings import clear_all_caches

        clear_all_caches()
        with patch("agent_tools._last_plan", return_value={"destination": "Singapore"}):
            out = agent_tools.generate_itinerary_pdf_tool.invoke({"nights": 0})
        assert out["filename"] == "Singapore-itinerary.pdf"
        from itinerary_store import get_itinerary_filename

        assert get_itinerary_filename(out["itinerary_id"]) == "Singapore-itinerary.pdf"

    def test_tips_for_an_uncurated_city_are_not_an_error(self) -> None:
        from agent_tools import get_destination_tips_tool

        out = get_destination_tips_tool.invoke({"destination": "Singapore"})
        assert not out.get("error") and out["curated"] is False
