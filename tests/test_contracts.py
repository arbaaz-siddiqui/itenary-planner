"""The contract is only useful if it is enforced. These tests enforce it.

Three jobs:

1. Every committed fixture still satisfies the models. The web UI builds cards
   against these files, so a fixture that drifts out of shape breaks the UI
   without touching any UI code.
2. The rules that exist to stop specific past bugs actually reject those bugs
   (the inline-row cap, mixed kinds in one envelope, unformatted facets).
3. `web/src/lib/contract.ts` still mirrors `contracts.py`. Adding a field on one
   side only is the failure mode that quietly produces `undefined` in a card,
   and it is invisible in review because the two files are rarely opened
   together.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import (
    MAX_INLINE_ROWS,
    Badge,
    Degraded,
    DetailSection,
    ImageRef,
    ItemDetail,
    MoreInfo,
    Row,
    Suggestion,
    ToolResult,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "contract"
TS_CONTRACT = ROOT / "web" / "src" / "lib" / "contract.ts"

KINDS = ["flights", "hotels", "tours", "transfers", "restaurants", "visa", "packages"]


def _row(**over) -> Row:
    base = dict(id="1", kind="tours", title="X")
    return Row(**{**base, **over})


# =============================================================================
# Fixtures round-trip
# =============================================================================
def test_every_kind_has_a_fixture():
    present = {p.stem for p in FIXTURES.glob("*.json")}
    assert present == set(KINDS), (
        f"missing {set(KINDS) - present}, unexpected {present - set(KINDS)}. "
        "Run: python -m scripts.build_contract_fixtures"
    )


@pytest.mark.parametrize("kind", KINDS)
def test_fixture_validates_and_round_trips(kind: str):
    raw = json.loads((FIXTURES / f"{kind}.json").read_text(encoding="utf-8"))
    result = ToolResult.model_validate(raw)

    assert result.kind == kind
    assert result.rows, "a fixture with no rows teaches the UI nothing"
    # Re-dumping must reproduce the file byte-for-byte in content, or the
    # committed fixture and the generator have diverged.
    assert result.model_dump(mode="json") == raw


@pytest.mark.parametrize("kind", KINDS)
def test_fixture_rows_are_renderable(kind: str):
    """Every row must carry what a card needs to be clickable and honest."""
    result = ToolResult.model_validate(
        json.loads((FIXTURES / f"{kind}.json").read_text(encoding="utf-8"))
    )
    for row in result.rows:
        assert row.id, f"{kind}: a row without an id cannot be clicked"
        assert row.detail_ref, f"{kind}: a row without detail_ref cannot open a drawer"
        assert row.title.strip(), f"{kind}: untitled row"
        if row.price_inr is not None:
            assert row.priced_for_pax >= 1, (
                f"{kind}: a price without a party size is the bug that multiplied "
                "a supplier total by the number of travellers"
            )


def test_a_degraded_fixture_exists():
    """The not-fresh path must be designed, not discovered by a customer."""
    degraded = [
        k
        for k in KINDS
        if ToolResult.model_validate(
            json.loads((FIXTURES / f"{k}.json").read_text(encoding="utf-8"))
        ).degraded
        is not None
    ]
    assert degraded, "no fixture exercises the degraded path"
    for k in degraded:
        result = ToolResult.model_validate(
            json.loads((FIXTURES / f"{k}.json").read_text(encoding="utf-8"))
        )
        assert result.degraded is not None
        assert result.degraded.disclosure.strip(), (
            "a degraded result must carry the sentence that discloses it; "
            "leaving the wording to the model is how stale prices got quoted as live"
        )


# =============================================================================
# The rules that stop specific past bugs
# =============================================================================
def test_inline_rows_are_capped():
    rows = [_row(id=str(i)) for i in range(MAX_INLINE_ROWS + 1)]
    with pytest.raises(ValidationError):
        ToolResult(kind="tours", rows=rows)


def test_rows_must_match_the_envelope_kind():
    with pytest.raises(ValidationError, match="must all be kind"):
        ToolResult(kind="tours", rows=[_row(kind="hotels")])


def test_facets_must_be_display_strings():
    """A facet is what the card prints. A raw number there means the renderer
    would have to format it, and then the agent and the card can disagree."""
    with pytest.raises(ValidationError, match="valid string"):
        _row(facets={"Duration": 6})  # type: ignore[arg-type]


def test_extra_fields_are_rejected():
    """The whole point of the envelope: you cannot bolt a new key onto a
    payload to fix a bug. Fix it in a value, or change the contract properly."""
    with pytest.raises(ValidationError):
        ToolResult(kind="tours", agent_instructions="paste this table verbatim")  # type: ignore[call-arg]


def test_unknown_kind_is_rejected():
    with pytest.raises(ValidationError):
        ToolResult(kind="cruises")  # type: ignore[arg-type]


def test_error_envelope_is_still_a_valid_result():
    """A tool never raises to the agent — it returns an envelope. The model must
    always have something true to say."""
    result = ToolResult(kind="hotels", error="Supplier returned 503 three times")
    assert result.rows == []
    assert result.is_empty() is False  # an error is not emptiness


def test_empty_is_distinct_from_error():
    assert ToolResult(kind="hotels").is_empty() is True


def test_ref_is_unguessable():
    a, b = ToolResult(kind="tours").ref, ToolResult(kind="tours").ref
    assert a != b
    assert len(a) > 12, "a ref addresses a payload, so it must not be enumerable"


# =============================================================================
# Detail
# =============================================================================
def test_item_detail_accepts_a_full_record():
    detail = ItemDetail(
        id="28488",
        kind="tours",
        title="Dubai Desert Safari with BBQ Dinner",
        images=[ImageRef(src="/api/img?src=x", ratio=1.777)],
        price_inr=1793.0,
        price_basis="per_adult",
        priced_for_pax=2,
        badges=[Badge(label="Free cancellation 24h", tone="ok")],
        sections=[
            DetailSection(
                heading="Transfer tiers",
                table=[
                    {"Tier": "Sharing", "Price": "₹1,793", "Basis": "per adult"},
                    {"Tier": "Private", "Price": "₹11,569", "Basis": "vehicle, up to 6"},
                ],
            ),
            DetailSection(heading="Inclusions", body=["BBQ dinner", "Belly dance show"]),
        ],
    )
    assert len(detail.sections) == 2
    assert detail.sections[0].table[1]["Basis"] == "vehicle, up to 6"


# =============================================================================
# The TypeScript mirror
# =============================================================================
def _ts_interface_fields(source: str, name: str) -> set[str]:
    """Field names declared on `export interface <name>`."""
    match = re.search(
        rf"export interface {name}\s*\{{(.*?)^\}}", source, re.S | re.M
    )
    assert match, f"interface {name} not found in contract.ts"
    body = match.group(1)
    # Strip block and line comments so a commented-out field is not counted.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r"^\s*(\w+)\s*[?]?\s*:", body, re.M))


@pytest.mark.parametrize(
    "model,ts_name",
    [
        (ToolResult, "ToolResult"),
        (Row, "Row"),
        (Badge, "Badge"),
        (ImageRef, "ImageRef"),
        (MoreInfo, "MoreInfo"),
        (Degraded, "Degraded"),
        (Suggestion, "Suggestion"),
        (DetailSection, "DetailSection"),
        (ItemDetail, "ItemDetail"),
    ],
)
def test_typescript_mirror_is_in_step(model, ts_name: str):
    source = TS_CONTRACT.read_text(encoding="utf-8")
    ts_fields = _ts_interface_fields(source, ts_name)
    py_fields = set(model.model_fields)
    assert py_fields == ts_fields, (
        f"{ts_name} has drifted.\n"
        f"  only in contracts.py:  {sorted(py_fields - ts_fields)}\n"
        f"  only in contract.ts:   {sorted(ts_fields - py_fields)}\n"
        "Add the field in BOTH files and regenerate the fixtures."
    )


def test_typescript_kinds_match():
    source = TS_CONTRACT.read_text(encoding="utf-8")
    match = re.search(r"export type Kind\s*=\s*(.*?);", source, re.S)
    assert match
    ts_kinds = set(re.findall(r'"(\w+)"', match.group(1)))
    assert ts_kinds == set(KINDS)


def test_typescript_row_cap_matches():
    source = TS_CONTRACT.read_text(encoding="utf-8")
    match = re.search(r"MAX_INLINE_ROWS\s*=\s*(\d+)", source)
    assert match and int(match.group(1)) == MAX_INLINE_ROWS
