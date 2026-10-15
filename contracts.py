"""contracts — the single shape every agent tool returns.

Why this module exists
----------------------
Before this, each tool returned a free-form dict of whatever its author needed
that day. Two things followed from that, and both are why the product had to be
rebuilt:

1. Every bug was fixed by adding a key to the dict and a paragraph of prose to
   an ``agent_instructions`` string *inside the payload*. Nothing was ever
   removed, because nothing could be shown to be redundant.
2. The web UI could not render real cards, because the payload had no stable
   shape. It parsed the model's rendered markdown table instead, so a card was a
   reconstruction of text the model typed -- no ids, no images, no detail, and
   rows silently lost whenever the model trimmed its table.

So: one envelope, enforced. New behaviour means new *values* in existing fields,
never new fields. A tool returns data and a rendered summary; it does not
lecture the model. Behavioural rules live in the system prompt, once.

The TypeScript mirror is ``web/src/lib/contract.ts``. The two are kept in step
by ``tests/test_contracts.py::test_typescript_mirror_is_in_step``, which fails
if a field is added on one side only.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# =============================================================================
# === VOCABULARY
# =============================================================================

#: The product families we sell. One `search_*` tool per kind, one card shape
#: per kind. Adding a kind is a deliberate act: it needs a card design, a
#: fixture and a detail renderer, so it must not be possible to invent one by
#: passing a new string.
Kind = Literal[
    "flights",
    "hotels",
    "tours",
    "transfers",
    "restaurants",
    "visa",
    "packages",
]

#: What a price is *for*. Never render a price without it. The supplier quotes
#: a tour's private transfer as one figure for the whole vehicle and a sharing
#: transfer as one figure per traveller, and reading the first as the second is
#: how a 6-person quote came out six times too high.
PriceBasis = Literal[
    "per_adult",
    "per_group",
    "per_room_night",
    "per_vehicle",
    "total",
    "on_request",
]

#: Colour role for a badge. Not a colour -- the theme decides the colour.
Tone = Literal["neutral", "ok", "warn", "danger"]

#: Why a suggestion is being offered. Drives the phrasing the model uses and
#: lets us measure which kinds of proactive nudge customers actually take.
SuggestionReason = Literal[
    "adjacent",    # you picked a hotel but have no airport transfer
    "cheaper",     # a materially cheaper row just outside the returned set
    "upgrade",     # a materially better row just outside the returned set
    "bundle",      # a package covers the same city and window for less
    "gap",         # a day in the window with nothing booked
    "constraint",  # near a supplier limit (9 pax, ~12 month horizon)
]

#: How a result was obtained. Anything other than "live" must be disclosed in
#: the reply -- see the degradation ladder in docs/RELEASE-2026.10-PLAN.md.
DegradedReason = Literal["cached", "stale", "partial", "empty", "unavailable"]


def new_ref(prefix: str = "ref") -> str:
    """An opaque artifact id. Unguessable, because a ref addresses a payload."""
    return f"{prefix}_{secrets.token_urlsafe(9)}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# === ROW PARTS
# =============================================================================
class Badge(BaseModel):
    """A short fact worth spotting at a glance: "Refundable", "2 stops"."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(max_length=40)
    tone: Tone = "neutral"


class ImageRef(BaseModel):
    """A picture for a card.

    `src` is always OUR url, never the supplier's. Supplier image hosts are
    unknown ahead of time and some 404, so they are fetched, cached and resized
    behind `/api/img`. `ratio` lets the card reserve space before the bytes
    arrive, which is what stops the deck reflowing as images land.
    """

    model_config = ConfigDict(extra="forbid")

    src: str
    alt: str = ""
    ratio: float = Field(default=1.5, gt=0.1, lt=10.0)


class Row(BaseModel):
    """One inline result. Small on purpose.

    A row is what the customer compares at a glance and what the model can hold
    in context cheaply. Everything else about the item lives in the artifact and
    is fetched by `detail_ref` when the customer opens it. That split is what
    keeps a 200KB supplier payload out of the model's context.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Kind
    title: str
    subtitle: str = ""

    price_inr: float | None = None
    price_basis: PriceBasis = "total"
    #: The party size these figures were quoted for. Travels WITH the price,
    #: because the same 1,793 read as per-head and multiplied by ten is how a
    #: ten-person quote came out at 115,690 when the supplier charges 23,138.
    priced_for_pax: int = 1

    badges: list[Badge] = Field(default_factory=list, max_length=6)
    image: ImageRef | None = None

    #: Opaque handle for `detail(kind, item_id)` and for the UI drawer. Both
    #: resolve it through the same call, so they cannot disagree about an item.
    detail_ref: str = ""

    #: Kind-specific display pairs, ALREADY FORMATTED for display
    #: ("Cancellation": "Free until 24h before"). Formatting here rather than in
    #: the renderer means the agent and the card show identical words. The
    #: `str` value type is load-bearing: it rejects a raw number, which would
    #: otherwise leave formatting to the renderer and let the card and the
    #: reply describe the same figure differently.
    facets: dict[str, str] = Field(default_factory=dict)


# =============================================================================
# === ENVELOPE PARTS
# =============================================================================
class MoreInfo(BaseModel):
    """There are more results than the ones inline."""

    model_config = ConfigDict(extra="forbid")

    total_available: int
    showing: int
    next_offset: int | None = None


class Degraded(BaseModel):
    """This result is not fresh live data, and the reply must say so.

    The rule this enforces: a fact we never checked is never reported as a
    supplier fact. A deadline-truncated enrichment previously reached the
    customer as "the supplier publishes no rates for this tour", which states
    something about the supplier that we had not established.
    """

    model_config = ConfigDict(extra="forbid")

    reason: DegradedReason
    #: How old the data is, when it is old rather than missing.
    stale_seconds: int | None = None
    #: Plain sentence the model can say. Written here so every surface says the
    #: same thing about the same failure.
    disclosure: str
    #: What we could not get, when only part of the result is missing.
    missing: list[str] = Field(default_factory=list)


class Suggestion(BaseModel):
    """"We also have this" -- computed, not improvised.

    Only the retrieval layer knows the catalogue, so only it can know that the
    customer has a free day, or that a package covers the same window for less.
    The prompt says once: if `suggest` is non-empty, mention at most two after
    answering the question. That is the whole implementation.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Kind
    reason: SuggestionReason
    #: One sentence, ready to say.
    text: str = Field(max_length=200)
    #: The call that acts on it, e.g. "search_transfers(from='DXB', to='Rove Downtown')".
    action: str = ""


# =============================================================================
# === THE ENVELOPE
# =============================================================================
#: Inline rows are capped hard. Five is what fits a chat column without
#: scrolling and what the model can quote without trimming. Everything beyond is
#: one fetch away through `ref`, which costs no supplier call.
MAX_INLINE_ROWS = 5


class ToolResult(BaseModel):
    """The only thing a search or detail tool may return.

    Adding a field here is a contract change: it needs the TypeScript mirror,
    a fixture update, and a card-renderer change. That friction is the point --
    it is what stops the payload growing a new key per bug.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(default_factory=new_ref)
    kind: Kind

    #: Finished markdown for the agent to paste. Built where the data is known,
    #: so the model never has to assemble a table and never drops a row.
    summary_md: str = ""

    rows: list[Row] = Field(default_factory=list, max_length=MAX_INLINE_ROWS)

    #: Result-wide facts: priced_for_pax, currency, as_of, total_available,
    #: search params echoed back. Values must be JSON-serialisable.
    facts: dict[str, Any] = Field(default_factory=dict)

    more: MoreInfo | None = None
    degraded: Degraded | None = None
    suggest: list[Suggestion] = Field(default_factory=list, max_length=3)

    #: Set when the call could not be served at all. A tool NEVER raises to the
    #: agent; it returns an envelope whose error is set, so the model always has
    #: something true to say.
    error: str | None = None

    @field_validator("rows")
    @classmethod
    def _rows_share_the_envelope_kind(cls, v: list[Row], info: Any) -> list[Row]:
        kind = (info.data or {}).get("kind")
        if kind is None:
            return v
        wrong = {r.kind for r in v if r.kind != kind}
        if wrong:
            raise ValueError(
                f"rows must all be kind {kind!r}; found {sorted(wrong)}. "
                "A mixed result needs one envelope per kind."
            )
        return v

    def is_empty(self) -> bool:
        return not self.rows and self.error is None


# =============================================================================
# === DETAIL
# =============================================================================
class DetailSection(BaseModel):
    """One labelled block in the drawer: "Rooms", "Cancellation", "Inclusions"."""

    model_config = ConfigDict(extra="forbid")

    heading: str
    #: Display pairs, in the order they should be shown.
    pairs: list[tuple[str, str]] = Field(default_factory=list)
    #: Free text paragraphs (a description, a policy).
    body: list[str] = Field(default_factory=list)
    #: A table when the block is genuinely tabular (rooms x rate x policy).
    table: list[dict[str, str]] = Field(default_factory=list)


class ItemDetail(BaseModel):
    """Everything we know about ONE item -- what the drawer renders.

    Produced by `detail(kind, item_id)`. The agent gets the same object when it
    needs to answer about a specific item, which is why the UI and the reply can
    never describe the same hotel differently.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Kind
    title: str
    subtitle: str = ""
    images: list[ImageRef] = Field(default_factory=list)
    price_inr: float | None = None
    price_basis: PriceBasis = "total"
    priced_for_pax: int = 1
    badges: list[Badge] = Field(default_factory=list)
    sections: list[DetailSection] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    degraded: Degraded | None = None
    error: str | None = None


__all__ = [
    "MAX_INLINE_ROWS",
    "Badge",
    "Degraded",
    "DegradedReason",
    "DetailSection",
    "ImageRef",
    "ItemDetail",
    "Kind",
    "MoreInfo",
    "PriceBasis",
    "Row",
    "Suggestion",
    "SuggestionReason",
    "Tone",
    "ToolResult",
    "new_ref",
    "utcnow",
]
