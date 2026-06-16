"""Tests for the hotel static-content additions from the N8N-Technoheven V1
collection: the two-token wiring, header builders, endpoint payloads, and the
shape-tolerant parsers.
"""

from __future__ import annotations

from typing import Any

import pytest

from parsers import (
    parse_hotel_cities_response,
    parse_hotel_descriptions_response,
    parse_hotel_guest_review_response,
    parse_hotel_static_data_response,
)


# =============================================================================
# Token wiring + headers
# =============================================================================
class TestTokenWiring:
    @staticmethod
    def _settings(token: str = "MAIN", hotel_static_token: str = "") -> Any:
        """Construct settings without reading the project .env (so explicit
        values aren't shadowed by real env values). Fields use validation
        aliases (BOOKING_TOKEN / BOOKING_HOTEL_STATIC_TOKEN), so pass by alias.
        """
        from settings import BookingApiSettings

        return BookingApiSettings(
            _env_file=None,
            BOOKING_TOKEN=token,
            BOOKING_HOTEL_STATIC_TOKEN=hotel_static_token,
        )

    def test_hotel_static_bearer_uses_dedicated_token(self) -> None:
        s = self._settings(token="MAIN", hotel_static_token="HOTEL")
        assert s.hotel_static_bearer() == "HOTEL"

    def test_hotel_static_bearer_falls_back_to_main(self) -> None:
        s = self._settings(token="MAIN", hotel_static_token="")
        assert s.hotel_static_bearer() == "MAIN"

    @staticmethod
    def _jwt(services: list[str]) -> str:
        """Build an unsigned JWT with a given serviceType claim (for scope check)."""
        import base64
        import json

        hdr = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
        pl = base64.urlsafe_b64encode(json.dumps({"serviceType": services}).encode())
        return f"{hdr}.{pl.decode().rstrip('=')}.sig"

    def test_all_services_token_has_no_missing(self) -> None:
        from settings import BookingApiSettings

        tok = self._jwt(["Hotels", "Flight", "Packages", "Restaurant", "Transfer", "Visa"])
        s = BookingApiSettings(_env_file=None, BOOKING_TOKEN=tok)
        assert s.main_token_missing_services() == []

    def test_activities_only_token_flags_missing(self) -> None:
        """An Activities-only token (the GT-018 mis-config) must be flagged —
        it silently returns null for hotels otherwise."""
        from settings import BookingApiSettings

        s = BookingApiSettings(_env_file=None, BOOKING_TOKEN=self._jwt(["Activities"]))
        missing = s.main_token_missing_services()
        assert "Hotels" in missing
        assert "Flight" in missing

    def test_undecodable_token_does_not_false_alarm(self) -> None:
        from settings import BookingApiSettings

        assert BookingApiSettings(_env_file=None, BOOKING_TOKEN="not.a.jwt").main_token_missing_services() == []
        assert BookingApiSettings(_env_file=None, BOOKING_TOKEN="").main_token_missing_services() == []

    def test_hotel_static_headers_use_hotel_token(self, monkeypatch: Any) -> None:
        import booking_api.headers as headers

        monkeypatch.setattr(
            headers,
            "get_booking_api_settings",
            lambda: self._settings(token="MAIN", hotel_static_token="HOTEL"),
        )
        h = headers.hotel_static_headers()
        assert h["Authorization"] == "Bearer HOTEL"
        assert h["x-accept-language"] == "en"
        assert h["Content-Type"] == "application/json"

    def test_base_headers_use_main_token(self, monkeypatch: Any) -> None:
        import booking_api.headers as headers

        monkeypatch.setattr(
            headers,
            "get_booking_api_settings",
            lambda: self._settings(token="MAIN", hotel_static_token="HOTEL"),
        )
        assert headers.base_headers()["Authorization"] == "Bearer MAIN"


# =============================================================================
# Endpoint payloads (mirror the collection exactly)
# =============================================================================
class TestEndpointPayloads:
    @pytest.fixture
    def capture(self, monkeypatch: Any) -> dict[str, Any]:
        """Patch the HTTP client so endpoints capture path/payload instead of
        making a real request."""
        import booking_api.endpoints as ep

        captured: dict[str, Any] = {}

        class _FakeClient:
            def post(self, path: str, *, json: dict, headers: dict) -> dict:
                captured["path"] = path
                captured["json"] = json
                captured["headers"] = headers
                return {}

        monkeypatch.setattr(ep, "get_client", lambda: _FakeClient())
        monkeypatch.setattr(ep, "hotel_static_headers", lambda: {"Authorization": "Bearer HOTEL"})
        return captured

    def test_cities_payload(self, capture: dict[str, Any]) -> None:
        from booking_api.endpoints import call_hotel_cities

        call_hotel_cities(city_name="Dubai")
        assert capture["path"] == "/api/xconnect/GetCitiesWithHotel"
        assert capture["json"] == {"Request": {"CityName": "Dubai"}}
        assert capture["headers"]["Authorization"] == "Bearer HOTEL"

    def test_static_by_city_payload(self, capture: dict[str, Any]) -> None:
        from booking_api.endpoints import call_hotel_static_by_city

        call_hotel_static_by_city(city_id=244520)
        assert capture["path"] == "/api/xconnect/GetStaticDataByCity"
        assert capture["json"] == {
            "Request": {"CityID": "244520", "Type": "city", "LocationId": ""}
        }

    def test_static_data_payload_uses_hotelids_caps(self, capture: dict[str, Any]) -> None:
        from booking_api.endpoints import call_hotel_static_data

        call_hotel_static_data(hotel_ids=[176, 177])
        assert capture["path"] == "/api/xconnect/GetHotelStaticDataOptimize"
        req = capture["json"]["Request"]
        assert req["HotelIDs"] == "176,177"  # caps 'IDs'
        assert capture["json"]["IsMobile"] == 1

    def test_descriptions_payload_uses_lowercase_hotelids(self, capture: dict[str, Any]) -> None:
        from booking_api.endpoints import call_hotel_descriptions

        call_hotel_descriptions(hotel_ids="176")
        assert capture["path"] == "/api/xconnect/GetPropertyDescriptions"
        req = capture["json"]["Request"]
        # GetPropertyDescriptions uses lowercase-s "HotelIds" per the collection
        assert "HotelIds" in req
        assert req["HotelIds"] == "176"
        assert capture["json"]["token"] == ""

    def test_guest_review_payload(self, capture: dict[str, Any]) -> None:
        from booking_api.endpoints import call_hotel_guest_review

        call_hotel_guest_review(hotel_id=176)
        assert capture["path"] == "/api/xconnect/GetHotelGuestReview"
        assert capture["json"] == {"Request": {"HotelId": 176}}


# =============================================================================
# Parsers — shape tolerance + normalization
# =============================================================================
class TestHotelStaticParsers:
    def test_cities_normalizes_envelope_variants(self) -> None:
        out = parse_hotel_cities_response(
            {"Result": {"Cities": [{"CityID": 244520, "CityName": "Dubai"}]}}
        )
        assert len(out) == 1
        assert out[0]["city_id"] == 244520
        assert out[0]["city_name"] == "Dubai"

    def test_cities_real_raw_shape_with_location_id(self) -> None:
        """REGRESSION: live GetCitiesWithHotel returns a `raw`-wrapped list with
        Id / LocationId / Type / Name (NOT a Cities/CityID envelope). The parser
        must extract these or the discovery flow silently returns 0 cities."""
        raw = {
            "raw": [
                {
                    "Id": 244520,
                    "LocationId": "6053839",
                    "FullName": "Dubai, Dubai, United Arab Emirates",
                    "Type": "city",
                    "Name": "Dubai",
                    "Rank": 5,
                }
            ]
        }
        out = parse_hotel_cities_response(raw)
        assert len(out) == 1
        c = out[0]
        assert c["city_id"] == 244520
        assert c["location_id"] == "6053839"  # feeds GetStaticDataByCity
        assert c["type"] == "city"
        assert c["city_name"] == "Dubai"

    def test_cities_lowercase_keys(self) -> None:
        out = parse_hotel_cities_response(
            {"result": {"list": [{"cityid": 1, "cityName": "X"}]}}
        )
        assert out[0]["city_id"] == 1
        assert out[0]["city_name"] == "X"

    def test_static_data_real_raw_shape(self) -> None:
        """REGRESSION: live GetStaticDataByCity returns a `raw`-wrapped list of
        {HotelId, StarRating, Category}. Must parse (was returning 0 hotels)."""
        raw = {
            "raw": [
                {"HotelId": 306, "IsRecommand": False, "Category": "Hotel", "StarRating": 5.0},
                {"HotelId": 451, "Category": "Hotel", "StarRating": 4.0},
            ]
        }
        out = parse_hotel_static_data_response(raw)
        assert len(out) == 2
        assert out[0]["hotel_id"] == 306
        assert out[0]["stars"] == 5.0

    def test_static_data_strips_html_and_parses_coords(self) -> None:
        out = parse_hotel_static_data_response(
            {
                "Result": {
                    "Hotels": [
                        {
                            "HotelId": 176,
                            "HotelName": "Test",
                            "StarRating": 4,
                            "Address": {
                                "FullAddress": "<b>St</b>",
                                "City": "Dubai",
                                "Latitude": "25.1",
                                "Longitude": "55.2",
                            },
                            "Facilities": [{"Name": "WiFi"}],
                            "Images": [{"Url": "http://x/a.jpg"}],
                        }
                    ]
                }
            }
        )
        h = out[0]
        assert h["hotel_id"] == 176
        assert h["full_address"] == "St"
        assert h["latitude"] == 25.1 and h["longitude"] == 55.2
        assert h["facilities"] == ["WiFi"]
        assert h["image_urls"] == ["http://x/a.jpg"]

    def test_static_data_skips_records_without_id(self) -> None:
        out = parse_hotel_static_data_response({"Hotels": [{"HotelName": "no id"}]})
        assert out == []

    def test_descriptions_with_sections(self) -> None:
        out = parse_hotel_descriptions_response(
            {
                "result": [
                    {
                        "HotelId": 176,
                        "Description": "<p>Nice</p>",
                        "Sections": [{"Title": "Dining", "Text": "Food"}],
                    }
                ]
            }
        )
        assert out[0]["description"] == "Nice"
        assert out[0]["sections"] == [{"title": "Dining", "text": "Food"}]

    def test_guest_review_summary(self) -> None:
        out = parse_hotel_guest_review_response(
            {
                "Result": {
                    "HotelId": 176,
                    "AverageRating": 8.4,
                    "TotalReviews": 1,
                    "Reviews": [{"Rating": 9, "Comment": "Great", "ReviewerName": "A"}],
                }
            }
        )
        assert out["hotel_id"] == 176
        assert out["average_rating"] == 8.4
        assert out["reviews"][0]["comment"] == "Great"

    @pytest.mark.parametrize(
        "parser",
        [
            parse_hotel_cities_response,
            parse_hotel_static_data_response,
            parse_hotel_descriptions_response,
        ],
    )
    def test_empty_response_returns_empty_list(self, parser: Any) -> None:
        assert parser({}) == []

    def test_guest_review_empty(self) -> None:
        out = parse_hotel_guest_review_response({})
        assert out["reviews"] == []
        assert out["total_reviews"] == 0


# =============================================================================
# Multi-room occupancy
# =============================================================================
class TestHotelRoomBuilder:
    def test_multi_room_assigns_room_numbers(self) -> None:
        from booking_api.endpoints import _build_hotel_rooms

        out = _build_hotel_rooms(
            rooms=[
                {"adults": 2, "children": 1, "child_ages": [5]},
                {"adults": 2},
            ],
            adults=2,
            children=0,
            child_ages=None,
        )
        assert [r["RoomNo"] for r in out] == [1, 2]
        assert out[0] == {"RoomNo": 1, "NoofAdults": 2, "NoOfChild": 1, "ChildAge": [5]}
        assert out[1] == {"RoomNo": 2, "NoofAdults": 2, "NoOfChild": 0, "ChildAge": []}

    def test_children_inferred_from_ages(self) -> None:
        from booking_api.endpoints import _build_hotel_rooms

        out = _build_hotel_rooms(
            rooms=[{"adults": 2, "child_ages": [5, 8]}],
            adults=2,
            children=0,
            child_ages=None,
        )
        assert out[0]["NoOfChild"] == 2
        assert out[0]["ChildAge"] == [5, 8]

    def test_fallback_single_room_from_flat_args(self) -> None:
        from booking_api.endpoints import _build_hotel_rooms

        out = _build_hotel_rooms(rooms=None, adults=3, children=1, child_ages=[6])
        assert out == [{"RoomNo": 1, "NoofAdults": 3, "NoOfChild": 1, "ChildAge": [6]}]

    def test_tolerates_supplier_keys(self) -> None:
        from booking_api.endpoints import _build_hotel_rooms

        out = _build_hotel_rooms(
            rooms=[{"NoofAdults": 1, "NoOfChild": 2, "ChildAge": [3, 9]}],
            adults=2,
            children=0,
            child_ages=None,
        )
        assert out[0]["NoofAdults"] == 1
        assert out[0]["ChildAge"] == [3, 9]


# =============================================================================
# Currency ROE — header wiring, endpoint path, and the get_exchange_rate tool
# =============================================================================
class TestCurrencyRoe:
    @staticmethod
    def _settings(token: str = "MAIN", roe_token: str = "") -> Any:
        from settings import BookingApiSettings

        return BookingApiSettings(
            _env_file=None,
            BOOKING_TOKEN=token,
            BOOKING_ROE_VERIFICATION_TOKEN=roe_token,
        )

    def test_headers_use_verification_token_when_set(self, monkeypatch: Any) -> None:
        import booking_api.headers as headers

        monkeypatch.setattr(
            headers, "get_booking_api_settings", lambda: self._settings(roe_token="VTOKEN")
        )
        h = headers.currency_roe_headers()
        assert h["RequestVerificationToken"] == "VTOKEN"
        assert "Authorization" not in h
        assert h["accept"] == "*/*"

    def test_headers_fall_back_to_bearer(self, monkeypatch: Any) -> None:
        import booking_api.headers as headers

        monkeypatch.setattr(
            headers, "get_booking_api_settings", lambda: self._settings(token="MAIN", roe_token="")
        )
        h = headers.currency_roe_headers()
        assert h["Authorization"] == "Bearer MAIN"
        assert "RequestVerificationToken" not in h

    def test_endpoint_builds_path_from_currency(self, monkeypatch: Any) -> None:
        import booking_api.endpoints as ep

        captured: dict[str, Any] = {}

        class _FakeClient:
            def get(self, path: str, *, headers: dict) -> dict:
                captured["path"] = path
                return {}

        monkeypatch.setattr(ep, "get_client", lambda: _FakeClient())
        monkeypatch.setattr(ep, "currency_roe_headers", lambda: {})
        ep.call_currency_roe(target_currency="inr")
        assert captured["path"] == "/api/Currency/ROE/INR"

    def test_tool_uses_live_rate(self, monkeypatch: Any) -> None:
        import mcp_tools.get_exchange_rate as tool

        monkeypatch.setattr(
            tool,
            "call_currency_roe",
            lambda **_: {"result": {"currencyCode": "INR", "sellingROE": 26.27}},
        )
        out = tool._impl()
        assert out["source"] == "live_api"
        assert out["rate"] == pytest.approx(26.27)
        assert out["base_currency"] == "AED"

    def test_tool_falls_back_to_manual_on_api_error(self, monkeypatch: Any) -> None:
        import mcp_tools.get_exchange_rate as tool
        from core import CurrencyRoeFailed

        def _boom(**_: Any) -> dict:
            raise CurrencyRoeFailed("down", endpoint="/api/Currency/ROE/INR")

        monkeypatch.setattr(tool, "call_currency_roe", _boom)
        out = tool._impl()
        assert out["source"] == "manual_fallback"
        assert out["rate"] == pytest.approx(23.0)  # CurrencySettings default
        assert out["live_error"] == "down"

    def test_tool_falls_back_when_response_has_no_rate(self, monkeypatch: Any) -> None:
        import mcp_tools.get_exchange_rate as tool

        monkeypatch.setattr(tool, "call_currency_roe", lambda **_: {"result": {}})
        out = tool._impl()
        assert out["source"] == "manual_fallback"

    def test_tool_honest_error_for_unsupported_pair(self, monkeypatch: Any) -> None:
        import mcp_tools.get_exchange_rate as tool

        monkeypatch.setattr(tool, "call_currency_roe", lambda **_: {})
        out = tool._impl(base_currency="JPY")
        assert out.get("error") is True
        assert out["error_type"] == "RateUnavailable"
