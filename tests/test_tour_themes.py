"""Tour themes come from the supplier's category, not from how a name sounds.

Client report, issue 4: asked for adventure tours, the agent recommended AYA
Universe (a light and sound show) and only conceded after the customer pushed
back.
"""

from types import SimpleNamespace
from unittest.mock import patch

from mcp_tools.search_tours import _impl, tour_themes


class _Row(SimpleNamespace):
    """A parsed tour row: any field the tool reads defaults to empty."""

    def __getattr__(self, _name: str):  # only called for missing fields
        return None

    def model_dump(self) -> dict:
        return dict(vars(self))


def _tour(name: str, category: str) -> _Row:
    return _Row(
        name=name,
        category=category,
        transfer_scenario="",
        is_recommended=False,
        price_per_adult_inr=1000.0,
        sharing_display="",
    )


def test_adventure_theme_has_no_theme_parks_or_shows() -> None:
    adventure = set(tour_themes()["adventure"])
    assert "adventure tours" in adventure and "desert safari tours" in adventure
    assert not adventure & {"theme parks", "attractions", "museums & galleries"}


def test_search_with_a_theme_keeps_only_that_kind() -> None:
    catalogue = [
        _tour("AYA Universe Dubai", "Theme Parks"),
        _tour("Dune Bashing Safari", "Desert Safari Tours"),
        _tour("Banana Boat Ride", "Water Activities"),
    ]
    with (
        patch("mcp_tools.search_tours.call_tour_search", return_value={}),
        patch("mcp_tools.search_tours.call_tour_rates", return_value={}),
        patch("mcp_tools.search_tours.parse_tour_response", return_value=catalogue),
        patch("mcp_tools.search_tours._recommended_first", side_effect=lambda p: p),
        patch("mcp_tools.search_tours.resolve_city", return_value={"city_id": 1, "country_id": 1}),
    ):
        out = _impl("Dubai", "2026-11-07", 2, theme="adventure", enrich=False)
    assert not out.get("error"), out
    names = [o["name"] for o in out["options"]]
    assert names == ["Dune Bashing Safari", "Banana Boat Ride"]
    assert out["theme"] == "adventure"


def test_a_unit_rate_is_a_vehicle_only_for_transfers() -> None:
    from mcp_tools.search_tours import _is_transfer_tour

    assert _is_transfer_tour(_tour("Airport Pickup (DXB)", "Airport Transfers"))
    assert not _tour("x", "y").sold_per_vehicle
    assert not _is_transfer_tour(_tour("Kayaking Experience in Dubai", "Water Activities"))
