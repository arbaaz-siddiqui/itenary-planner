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


# =============================================================================
# Amount coercion — regression for the "every field says On Request" bug
# =============================================================================
class TestAmountCoercion:
    """Amounts reach us from LLM tool calls, so they arrive as strings as often
    as numbers. Treating a numeric string as "no price" made every priced row
    in the PDF render "On Request" — the bug the client reported.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (217366, 217366.0),          # int
            (217366.5, 217366.5),        # float
            ("217366", 217366.0),        # plain numeric string
            ("1,24,500", 124500.0),      # Indian digit grouping
            ("45000.00", 45000.0),       # decimal string
            ("₹ 12,000", 12000.0),  # rupee symbol
            ("Rs 8,500", 8500.0),        # Rs prefix
            ("INR 9,000", 9000.0),       # currency code
            ("  7500  ", 7500.0),        # whitespace
        ],
    )
    def test_parses_real_amounts(self, raw: Any, expected: float) -> None:
        doc = itinerary_doc_from_dict({"components": [{"label": "X", "amount_inr": raw}]})
        assert doc.components[0].amount_inr == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "ask supplier", "On Request", [], {}])
    def test_absent_or_unparseable_stays_on_request(self, raw: Any) -> None:
        doc = itinerary_doc_from_dict({"components": [{"label": "X", "amount_inr": raw}]})
        assert doc.components[0].amount_inr is None

    @pytest.mark.parametrize("raw", [True, False])
    def test_bool_is_not_a_price(self, raw: Any) -> None:
        """bool subclasses int, so True would otherwise render as Rs 1."""
        doc = itinerary_doc_from_dict({"components": [{"label": "X", "amount_inr": raw}]})
        assert doc.components[0].amount_inr is None

    def test_string_amounts_keep_payment_installments(self) -> None:
        """A string amount used to DROP the whole installment row silently."""
        doc = itinerary_doc_from_dict(
            {
                "payment_schedule": [
                    {"label": "Deposit", "amount_inr": "78,598", "due_date_iso": "2026-06-10"},
                    {"label": "Balance", "amount_inr": 183396, "due_date_iso": "2026-06-25"},
                    {"label": "Bogus", "amount_inr": "TBD"},  # genuinely unparseable -> skipped
                ]
            }
        )
        assert [i.label for i in doc.payment_schedule] == ["Deposit", "Balance"]
        assert doc.payment_schedule[0].amount_inr == 78598.0

    def test_string_total_and_nights(self) -> None:
        doc = itinerary_doc_from_dict({"total_inr": "2,61,994", "nights": "3"})
        assert doc.total_inr == 261994.0
        assert doc.nights == 3


# =============================================================================
# Day plans — regression for raw dicts printed into the PDF
# =============================================================================
class TestDayItems:
    """The agent sends day entries as dicts. They were flattened with str(x),
    so the PDF printed a literal Python dict:
        {'detail': 'Private transfer to hotel', 'kind': 'transfer', ...}
    """

    def test_dict_items_are_parsed_into_fields(self) -> None:
        doc = itinerary_doc_from_dict(
            {
                "day_plans": [
                    {
                        "title": "Day 1 - Arrival",
                        "items": [
                            {
                                "title": "Arrival at DXB",
                                "start": "12:25",
                                "detail": "Private transfer to hotel",
                                "kind": "transfer",
                            }
                        ],
                    }
                ]
            }
        )
        item = doc.day_plans[0].items[0]
        assert item.title == "Arrival at DXB"
        assert item.start == "12:25"
        assert item.detail == "Private transfer to hotel"
        assert item.kind == "transfer"

    def test_plain_strings_still_supported(self) -> None:
        doc = itinerary_doc_from_dict(
            {"day_plans": [{"title": "Day 1", "items": ["Pickup", "Check-in"]}]}
        )
        assert [i.title for i in doc.day_plans[0].items] == ["Pickup", "Check-in"]
        assert all(i.start == "" for i in doc.day_plans[0].items)

    def test_alias_keys_are_accepted(self) -> None:
        doc = itinerary_doc_from_dict(
            {
                "day_plans": [
                    {
                        "title": "Day 2",
                        "items": [{"name": "Museum", "time": "10:00",
                                   "description": "Culture", "type": "tour"}],
                    }
                ]
            }
        )
        item = doc.day_plans[0].items[0]
        assert (item.title, item.start, item.detail, item.kind) == (
            "Museum", "10:00", "Culture", "tour",
        )

    def test_unknown_dict_shape_degrades_to_text_not_repr(self) -> None:
        """Never print a Python dict; fall back to its values as text."""
        doc = itinerary_doc_from_dict(
            {"day_plans": [{"title": "Day 3", "items": [{"weird": "Sunset cruise"}]}]}
        )
        rendered = doc.day_plans[0].items[0].title
        assert "Sunset cruise" in rendered
        assert "{" not in rendered and "weird" not in rendered

    @pytest.mark.parametrize("bad", [None, "", [], {}, 0])
    def test_empty_items_are_dropped(self, bad: Any) -> None:
        doc = itinerary_doc_from_dict({"day_plans": [{"title": "D", "items": [bad]}]})
        assert doc.day_plans[0].items == []

    def test_visa_section_is_carried_into_the_doc(self) -> None:
        """Visa was re-asked at PDF time because the tool had no visa field."""
        doc = itinerary_doc_from_dict(
            {
                "visa": {
                    "visa_type": "30 Days Single Entry Tourist Visa",
                    "entry_type": "Single",
                    "stay_duration": "30 Days",
                    "processing": "Confirm with supplier",
                    "price_display": "On Request",
                    "documents": ["Passport Copy", "Passport Size Photograph"],
                }
            }
        )
        assert doc.visa is not None
        assert doc.visa.visa_type.startswith("30 Days")
        assert doc.visa.documents == ["Passport Copy", "Passport Size Photograph"]
        pdf = build_itinerary_pdf(doc)
        assert pdf[:5] == b"%PDF-"

    @pytest.mark.parametrize("raw", [None, {}, "", [], {"visa_type": "", "documents": []}])
    def test_absent_visa_renders_no_section(self, raw: Any) -> None:
        doc = itinerary_doc_from_dict({"visa": raw})
        assert doc.visa is None
        assert build_itinerary_pdf(doc)[:5] == b"%PDF-"

    def test_visa_accepts_alias_keys(self) -> None:
        doc = itinerary_doc_from_dict(
            {"visa": {"type": "Tourist", "entry": "Multiple",
                      "stay": "60 Days", "processing_display": "3-4 days"}}
        )
        assert doc.visa is not None
        assert (doc.visa.visa_type, doc.visa.entry_type) == ("Tourist", "Multiple")
        assert doc.visa.stay_duration == "60 Days"
        assert doc.visa.processing == "3-4 days"

    def test_renders_without_dict_repr_in_pdf(self) -> None:
        """End-to-end: the rendered bytes must not contain a dict repr."""
        doc = itinerary_doc_from_dict(
            {
                "day_plans": [
                    {
                        "title": "Day 1 - Arrival",
                        "items": [{"title": "Arrival at DXB", "start": "12:25",
                                   "detail": "Private transfer", "kind": "transfer"}],
                    }
                ]
            }
        )
        pdf = build_itinerary_pdf(doc)
        assert pdf[:5] == b"%PDF-"
        assert b"'kind':" not in pdf
        assert b"'detail':" not in pdf

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
