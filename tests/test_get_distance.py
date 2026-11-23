"""get_distance answers "how far is X from Y" with no dates (23 Nov 2026).

The agent used to reach for the airport-transfer search, which needs a travel
date, and asked "When do you need the transfer?" instead of answering.
"""

from __future__ import annotations

from typing import Any

import pytest

import mcp_tools.get_distance as gd
from geo import Place

ATLANTIS = Place(name="Atlantis, The Palm", latitude=25.1304, longitude=55.1171,
                 source_type="hotel", city="Dubai")


@pytest.fixture
def places(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    known = {"atlantis the palm": ATLANTIS}
    monkeypatch.setattr(gd, "locate_place", lambda q, c="": known.get(q.strip().lower()))
    return known


def test_hotel_to_airport_needs_no_date(places) -> None:
    r = gd._impl("Atlantis The Palm", "Dubai airport", "Dubai")
    assert not r.get("error")
    assert r["to"].endswith("(DXB)")
    assert 27 < r["straight_line_km"] < 31
    lo, hi = r["approx_drive_minutes"]
    assert 0 < lo <= hi
    assert "date" in r["agent_instructions"].lower()  # "do not ask for travel dates"


def test_airport_by_code(places) -> None:
    r = gd._impl("DXB", "Atlantis The Palm", "Dubai")
    assert r["from"].endswith("(DXB)")


def test_curated_landmark_is_placed(places) -> None:
    """Burj Khalifa is not placed by the autocomplete; the Dubai attraction
    table knows it."""
    r = gd._impl("Atlantis The Palm", "Burj Khalifa", "Dubai")
    assert not r.get("error") and r["straight_line_km"] > 5


def test_unknown_place_is_not_estimated(places) -> None:
    r = gd._impl("Zzqx Inn", "Dubai airport", "Dubai")
    assert r["error"] and r["error_type"] == "PlaceNotFound"
    assert "straight_line_km" not in r


def test_tool_is_registered() -> None:
    from agent_tools import _build_all_tools

    assert "get_distance" in {t.name for t in _build_all_tools()}
