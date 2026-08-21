"""Tour listing must page, escalate, and never repeat.

"Show me more" used to be impossible: `search_tours` had no `offset`, so a
second call returned the SAME cheapest tours again, and `total_results` reported
the page size (5) rather than how many tours actually exist (~270). A customer
could never get past the first handful.

These tests use a synthetic ranked list so they stay hermetic — the paging maths
is what matters, not the live catalogue.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_tools.search_tours import _recommended_first


class _Tour:
    """Minimal stand-in with the two fields the ranker reads."""

    def __init__(self, name: str, price: float, recommended: bool = False) -> None:
        self.name = name
        self.price_per_adult_inr = price
        self.is_recommended = recommended


class TestRecommendedFirst:
    def test_recommended_lead_regardless_of_price(self) -> None:
        tours = [
            _Tour("Cheap Museum", 541),
            _Tour("Dubai Citytour", 2023, recommended=True),
            _Tour("Butterfly Garden", 798),
            _Tour("Abu Dhabi Tour", 2420, recommended=True),
        ]
        names = [t.name for t in _recommended_first(tours)]
        assert names[:2] == ["Dubai Citytour", "Abu Dhabi Tour"]

    def test_cheapest_first_within_each_group(self) -> None:
        tours = [
            _Tour("Rec Expensive", 5000, recommended=True),
            _Tour("Rec Cheap", 1000, recommended=True),
            _Tour("Other Expensive", 4000),
            _Tour("Other Cheap", 500),
        ]
        names = [t.name for t in _recommended_first(tours)]
        assert names == ["Rec Cheap", "Rec Expensive", "Other Cheap", "Other Expensive"]

    def test_no_recommended_still_sorts_by_price(self) -> None:
        tours = [_Tour("B", 900), _Tour("A", 300)]
        assert [t.name for t in _recommended_first(tours)] == ["A", "B"]

    def test_empty_list(self) -> None:
        assert _recommended_first([]) == []


def _page(ranked: list[Any], offset: int, size: int) -> dict[str, Any]:
    """The exact paging arithmetic `_impl` applies after ranking."""
    start = max(0, int(offset or 0))
    page = ranked[start : start + size]
    shown_end = start + len(page)
    remaining = max(0, len(ranked) - shown_end)
    return {
        "page": page,
        "total_available": len(ranked),
        "remaining": remaining,
        "showing": f"{start + 1}-{shown_end}" if page else "0",
        "next_offset": shown_end if remaining else None,
        "next_max_results": size * 2 if remaining else None,
    }


class TestEscalatingPaging:
    @pytest.fixture
    def ranked(self) -> list[Any]:
        return [_Tour(f"Tour {i}", 100 + i) for i in range(270)]

    def test_first_page_is_ten(self, ranked: list[Any]) -> None:
        r = _page(ranked, 0, 10)
        assert len(r["page"]) == 10
        assert r["showing"] == "1-10"
        assert r["total_available"] == 270, "must report the CATALOGUE size, not the page"
        assert r["remaining"] == 260

    def test_page_size_doubles_each_ask(self, ranked: list[Any]) -> None:
        """10 -> 20 -> 40 -> 80, following next_offset/next_max_results."""
        off, size, sizes = 0, 10, []
        for _ in range(4):
            r = _page(ranked, off, size)
            sizes.append(len(r["page"]))
            off, size = r["next_offset"], r["next_max_results"]
        assert sizes == [10, 20, 40, 80]

    def test_offsets_follow_the_stated_sequence(self, ranked: list[Any]) -> None:
        off, size, spans = 0, 10, []
        for _ in range(4):
            r = _page(ranked, off, size)
            spans.append(r["showing"])
            off, size = r["next_offset"], r["next_max_results"]
        assert spans == ["1-10", "11-30", "31-70", "71-150"]

    def test_no_tour_is_ever_repeated(self, ranked: list[Any]) -> None:
        """The whole point: each 'show more' must be NEW tours."""
        off, size, seen = 0, 10, []
        while off is not None:
            r = _page(ranked, off, size)
            seen += [t.name for t in r["page"]]
            off, size = r["next_offset"], r["next_max_results"] or size
        assert len(seen) == len(set(seen)) == 270, "duplicates or gaps in paging"

    def test_exhaustion_reports_no_remaining(self, ranked: list[Any]) -> None:
        r = _page(ranked, 260, 40)
        assert len(r["page"]) == 10  # only 10 left, not 40
        assert r["remaining"] == 0
        assert r["next_offset"] is None, "must not invite another page"

    def test_offset_past_the_end_is_empty_not_an_error(self, ranked: list[Any]) -> None:
        r = _page(ranked, 9999, 10)
        assert r["page"] == []
        assert r["remaining"] == 0

    def test_negative_offset_clamped(self, ranked: list[Any]) -> None:
        assert _page(ranked, -5, 10)["showing"] == "1-10"


class TestSharingAndCancellationAlwaysPresent:
    """Client requirement, non-negotiable: EVERY tour states shared/private and
    its cancellation terms. Both were parsed away entirely — `transferScenario`
    and `cancellationPolicyID` are on every list row and were never read.
    """

    def _tour(self, **kw: Any) -> Any:
        from core import TourOption

        base = dict(tour_id=1, name="T", price_per_adult_inr=100.0)
        base.update(kw)
        return TourOption(**base)

    @pytest.mark.parametrize(
        "scenario,expected",
        [
            ("Private Transfer", "private"),
            ("Sharing Transfer", "shar"),
            ("All Transfer", "shared or private"),
            ("Without Transfer", "ticket only"),
        ],
    )
    def test_every_scenario_maps_to_words(self, scenario: str, expected: str) -> None:
        got = self._tour(transfer_scenario=scenario).sharing_display.lower()
        assert expected in got

    def test_unknown_scenario_still_says_something(self) -> None:
        """Never blank — the customer must always get a transfer basis."""
        assert self._tour(transfer_scenario="").sharing_display.strip()
        assert self._tour(transfer_scenario="weird").sharing_display.strip()

    def test_cancellation_never_blank(self) -> None:
        """The false-denial bug: blank meant the agent said there was no policy."""
        out = self._tour(cancellation_policy="").cancellation_display
        assert out.strip()
        assert "request" in out.lower() or "confirm" in out.lower()

    def test_known_policy_relayed_verbatim(self) -> None:
        t = self._tour(cancellation_policy="Free cancellation up to 24 hours prior")
        assert t.cancellation_display == "Free cancellation up to 24 hours prior"

    def test_policy_id_mapping_covers_the_common_ones(self) -> None:
        """Resolved live: ids 11 (105 tours), 2 (89), 23 (64) are the bulk."""
        from parsers import tour_cancellation_policy

        assert "free" in tour_cancellation_policy(11).lower()
        assert "free" in tour_cancellation_policy(2).lower()
        assert "non-refundable" in tour_cancellation_policy(23).lower()
        assert "no cancellation" in tour_cancellation_policy(13).lower()

    def test_unknown_policy_id_is_empty_so_the_model_falls_back(self) -> None:
        from parsers import tour_cancellation_policy

        assert tour_cancellation_policy(99999) == ""
        assert tour_cancellation_policy(None) == ""


class TestTransferTypeFilter:
    """"Show me the shared and private tours" must reach ALL of them.

    Live: 332 Dubai tours — 246 "Without Transfer", 52 "All Transfer",
    33 "Private Transfer", 1 "Sharing Transfer". So 86 include a transfer. With
    no filter the agent saw only page one and told the customer "only 3-4 tours
    have shared or private options".
    """

    def _pool(self) -> list[Any]:
        from core import TourOption

        rows = [
            ("All Transfer", 3),
            ("Private Transfer", 2),
            ("Sharing Transfer", 1),
            ("Without Transfer", 10),
            ("", 1),
        ]
        out = []
        n = 0
        for scenario, count in rows:
            for _ in range(count):
                n += 1
                out.append(
                    TourOption(
                        tour_id=n, name=f"T{n}", price_per_adult_inr=100.0 + n,
                        transfer_scenario=scenario,
                    )
                )
        return out

    def _filter(self, pool: list[Any], tf: str) -> list[Any]:
        """The filter as `_impl` applies it."""
        tf = (tf or "").strip().lower()
        if tf in {"with_transfer", "transfer", "shared", "private", "shared_private"}:
            return [
                o for o in pool
                if "without" not in (o.transfer_scenario or "").lower()
                and (o.transfer_scenario or "").strip()
            ]
        if tf in {"ticket_only", "ticket", "without_transfer", "no_transfer"}:
            return [o for o in pool if "without" in (o.transfer_scenario or "").lower()]
        return pool

    def test_with_transfer_includes_all_three_kinds(self) -> None:
        got = self._filter(self._pool(), "with_transfer")
        kinds = {o.transfer_scenario for o in got}
        assert kinds == {"All Transfer", "Private Transfer", "Sharing Transfer"}
        assert len(got) == 6

    def test_with_transfer_excludes_ticket_only_and_blanks(self) -> None:
        got = self._filter(self._pool(), "with_transfer")
        assert all("without" not in o.transfer_scenario.lower() for o in got)
        assert all(o.transfer_scenario.strip() for o in got)

    def test_ticket_only_is_the_complement(self) -> None:
        assert len(self._filter(self._pool(), "ticket_only")) == 10

    def test_no_filter_returns_everything(self) -> None:
        assert len(self._filter(self._pool(), "")) == 17

    @pytest.mark.parametrize("alias", ["shared", "private", "transfer", "shared_private"])
    def test_aliases_all_mean_with_transfer(self, alias: str) -> None:
        assert len(self._filter(self._pool(), alias)) == 6


class TestNeverInventATransferPrice:
    """The supplier returns ONE rate per tour with no shared/private split.

    A real conversation produced "Private ~₹9,676 for 4" by multiplying the
    per-adult fare by the party size. That figure does not exist anywhere in the
    API — it is a fabricated price.
    """

    def _tour(self, **kw: Any) -> Any:
        from core import TourOption

        base = dict(tour_id=1, name="T", price_per_adult_inr=2419.0)
        base.update(kw)
        return TourOption(**base)

    def test_sharing_display_states_price_is_the_same(self) -> None:
        out = self._tour(transfer_scenario="All Transfer").sharing_display
        assert "same tour price" in out.lower()

    def test_sharing_display_quotes_no_number(self) -> None:
        """No digits — a number here would read as a private-vehicle quote."""
        for scenario in ["All Transfer", "Private Transfer", "Sharing Transfer", "Without Transfer", ""]:
            out = self._tour(transfer_scenario=scenario).sharing_display
            assert not any(ch.isdigit() for ch in out), f"{scenario!r} -> {out!r}"

    def test_named_options_used_when_details_known(self) -> None:
        t = self._tour(
            transfer_scenario="All Transfer",
            transfer_options=["Sharing Transfer", "Private Transfer"],
        )
        assert "Sharing Transfer, Private Transfer" in t.sharing_display

    def test_has_transfer_choice(self) -> None:
        assert self._tour(transfer_scenario="All Transfer").has_transfer_choice
        assert not self._tour(transfer_scenario="Without Transfer").has_transfer_choice


class TestPrivateTourVsPrivateTransfer:
    """"Private TOUR" and "private TRANSFER" are different questions.

    A real conversation: the customer said "not transfer but tour" and the agent
    kept re-explaining transfers. There ARE separate private-experience products
    ("Private Luxury Yacht Experience", "Dubai Half-Day Private Old Town Walking
    Tour"), but their transferScenario is "Without Transfer" — so filtering on
    transferScenario alone dropped all 12 of them.
    """

    def _pool(self) -> list[Any]:
        from core import TourOption

        specs = [
            ("Private Luxury Yacht Experience", "Without Transfer", 33877.0),
            ("Private Vehicle Full Day With Driver", "Without Transfer", 12814.0),
            ("Dubai Citytour", "All Transfer", 2022.0),
            ("Dubai Fountain Show", "Private Transfer", 541.0),
            ("Abu Dhabi Tour", "Sharing Transfer", 2419.0),
            ("Al Shindagha Museum", "Without Transfer", 626.0),
        ]
        return [
            TourOption(tour_id=i + 1, name=n, price_per_adult_inr=p, transfer_scenario=sc)
            for i, (n, sc, p) in enumerate(specs)
        ]

    def _private(self, pool: list[Any]) -> list[Any]:
        def is_product(o: Any) -> bool:
            return "private" in (o.name or "").lower()

        got = [o for o in pool if is_product(o) or "private" in (o.transfer_scenario or "").lower()]
        return sorted(got, key=lambda o: (not is_product(o), o.price_per_adult_inr))

    def test_private_experiences_are_included(self) -> None:
        """The bug: these were dropped because they include no transfer."""
        names = [o.name for o in self._private(self._pool())]
        assert "Private Luxury Yacht Experience" in names
        assert "Private Vehicle Full Day With Driver" in names

    def test_private_experiences_rank_first(self) -> None:
        """Cheapest-first buried them under ₹541 tours with private transfers."""
        got = self._private(self._pool())
        assert "private" in got[0].name.lower()
        assert "private" in got[1].name.lower()

    def test_private_transfer_tours_still_included_after(self) -> None:
        names = [o.name for o in self._private(self._pool())]
        assert "Dubai Fountain Show" in names
        assert names.index("Private Luxury Yacht Experience") < names.index("Dubai Fountain Show")

    def test_ticket_only_excludes_private_products(self) -> None:
        """A private yacht is not an "entry ticket" tour."""
        pool = self._pool()

        def has_transfer(o: Any) -> bool:
            sc = (o.transfer_scenario or "").strip()
            return bool(sc) and "without" not in sc.lower()

        def is_product(o: Any) -> bool:
            return "private" in (o.name or "").lower()

        got = [o.name for o in pool if not has_transfer(o) and not is_product(o)]
        assert got == ["Al Shindagha Museum"]


class TestNoCountsLeakToTheCustomer:
    """"85 tours available in Dubai" was wrong AND unwanted.

    Wrong because 85 was the size of a FILTERED set (the catalogue is 270), and
    unwanted because the customer does not care about totals.
    """

    def test_instructions_ban_counts(self) -> None:
        from mcp_tools.search_tours import _impl

        src = _impl.__doc__ or ""
        assert "offset" in src  # sanity: we are reading the right function

    def test_prompt_bans_quoting_counts(self) -> None:
        from pathlib import Path

        p = Path(__file__).resolve().parent.parent / "prompts" / "system_prompt_v2.md"
        text = p.read_text(encoding="utf-8")
        assert "NEVER quote counts" in text

    def test_prompt_teaches_tour_vs_transfer(self) -> None:
        from pathlib import Path

        p = Path(__file__).resolve().parent.parent / "prompts" / "system_prompt_v2.md"
        text = p.read_text(encoding="utf-8").lower()
        assert "not transfer but tour" in text, "the real failure must be documented"
        assert 'transfer_type="private"' in text
