"""Tests for itinerary PDF generation, storage, and the serving route.

Covers the branded-PDF feature end to end: the builder produces a valid PDF
with the letterhead banners, the dict coercion is tolerant, the store saves /
retrieves by id (with a path-traversal guard), and the FastAPI route serves it
back (the URL Twilio fetches for WhatsApp delivery).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from itinerary_pdf import (
    ItineraryDoc,
    build_itinerary_pdf,
    itinerary_doc_from_dict,
)

_SAMPLE: dict[str, Any] = {
    "customer_name": "Mr. Sharma",
    "origin_city": "Mumbai",
    "destination": "Dubai",
    "start_date": "2026-07-03",
    "end_date": "2026-07-06",
    "nights": 3,
    "party_summary": "2 adults",
    "reference": "GT-2026-0042",
    "overview": "A 3-night Dubai getaway.",
    "day_plans": [{"title": "Day 1 - Arrival", "items": ["Pickup", "Check-in"]}],
    "components": [
        {"label": "Flights", "detail": "BOM->DXB return", "amount_inr": 217366},
        {"label": "UAE Visa", "amount_inr": None},  # On Request
    ],
    "inclusions": ["Return flights", "3 nights hotel"],
    "exclusions": ["Travel insurance"],
    "total_inr": 261994,
    "payment_schedule": [
        {"label": "Deposit", "amount_inr": 78598, "due_date_iso": "2026-06-10"},
        {"label": "Balance", "amount_inr": 183396, "due_date_iso": "2026-06-25"},
    ],
    "notes": ["Apply visa 5-7 working days before travel"],
}


# =============================================================================
# Builder + model
# =============================================================================
class TestBuilder:
    def test_produces_valid_pdf_bytes(self) -> None:
        doc = itinerary_doc_from_dict(_SAMPLE)
        pdf = build_itinerary_pdf(doc)
        assert isinstance(pdf, bytes)
        assert pdf[:5] == b"%PDF-"
        assert len(pdf) > 5000  # banners + content present

    def test_minimal_doc_still_renders(self) -> None:
        """An almost-empty itinerary must not crash (degrade gracefully)."""
        pdf = build_itinerary_pdf(ItineraryDoc(destination="Dubai"))
        assert pdf[:5] == b"%PDF-"

    def test_doc_from_dict_coerces_types(self) -> None:
        doc = itinerary_doc_from_dict(_SAMPLE)
        assert doc.nights == 3
        assert len(doc.components) == 2
        assert doc.components[1].amount_inr is None  # On Request preserved
        assert doc.total_inr == 261994.0
        assert len(doc.payment_schedule) == 2
        assert doc.day_plans[0].title == "Day 1 - Arrival"

    def test_doc_from_dict_ignores_garbage(self) -> None:
        doc = itinerary_doc_from_dict(
            {"nights": "not-a-number", "components": ["bad"], "total_inr": None, "unknown": 1}
        )
        assert doc.nights is None
        assert doc.components == []
        assert doc.total_inr is None

    def test_unicode_does_not_crash_core_fonts(self) -> None:
        """Rupee sign, en-dashes, curly quotes must fold to latin-1 safely."""
        doc = itinerary_doc_from_dict(
            {
                "overview": "Trip — ₹50,000 with the “best” hotels",
                "components": [{"label": "Hotel — Rove", "amount_inr": 33528}],
                "notes": ["Don’t forget the visa"],
            }
        )
        pdf = build_itinerary_pdf(doc)
        assert pdf[:5] == b"%PDF-"


# =============================================================================
# Store
# =============================================================================
@pytest.fixture
def store(tmp_path: Path, monkeypatch: Any):
    """Point ITINERARY_DIR at a tmp dir and return the (re-imported) store."""
    monkeypatch.setenv("ITINERARY_DIR", str(tmp_path / "itins"))
    from settings import clear_all_caches

    clear_all_caches()
    import itinerary_store

    return itinerary_store


class TestStore:
    def test_save_and_retrieve(self, store: Any) -> None:
        iid, path = store.save_itinerary_pdf(_SAMPLE)
        assert path.exists()
        assert path.read_bytes()[:5] == b"%PDF-"
        assert store.get_itinerary_path(iid) == path

    def test_id_is_stable_for_same_content(self, store: Any) -> None:
        id1, _ = store.save_itinerary_pdf(_SAMPLE)
        id2, _ = store.save_itinerary_pdf(_SAMPLE)
        assert id1 == id2

    def test_different_content_different_id(self, store: Any) -> None:
        id1, _ = store.save_itinerary_pdf(_SAMPLE)
        id2, _ = store.save_itinerary_pdf({**_SAMPLE, "total_inr": 999999})
        assert id1 != id2

    def test_missing_id_returns_none(self, store: Any) -> None:
        assert store.get_itinerary_path("doesnotexist99") is None

    def test_traversal_guard(self, store: Any) -> None:
        """Non-alphanumeric ids are rejected (no path traversal)."""
        assert store.get_itinerary_path("../secret") is None
        assert store.get_itinerary_path("a/b") is None

    def test_public_url_none_without_base(self, store: Any, monkeypatch: Any) -> None:
        # Build settings WITHOUT the project .env so a real PUBLIC_BASE_URL there
        # can't leak in; an empty base must yield None.
        from settings import StateSettings

        monkeypatch.setattr(
            store, "get_state_settings", lambda: StateSettings(_env_file=None, PUBLIC_BASE_URL="")
        )
        assert store.public_url_for("abc123") is None

    def test_public_url_built_with_base(self, store: Any, monkeypatch: Any) -> None:
        from settings import StateSettings

        monkeypatch.setattr(
            store,
            "get_state_settings",
            lambda: StateSettings(_env_file=None, PUBLIC_BASE_URL="https://example.up.railway.app/"),
        )
        assert store.public_url_for("abc123") == "https://example.up.railway.app/itinerary/abc123.pdf"


# =============================================================================
# Serving route (the URL Twilio fetches)
# =============================================================================
class TestServeRoute:
    def test_serves_pdf_and_guards(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("ITINERARY_DIR", str(tmp_path / "itins"))
        from settings import clear_all_caches

        clear_all_caches()
        from fastapi.testclient import TestClient

        import itinerary_store
        import surfaces.whatsapp_app as w

        iid, _ = itinerary_store.save_itinerary_pdf(_SAMPLE)
        client = TestClient(w.app)

        ok = client.get(f"/itinerary/{iid}.pdf")
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "application/pdf"
        assert ok.content[:5] == b"%PDF-"

        assert client.get("/itinerary/missing12345.pdf").status_code == 404
