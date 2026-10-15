"""search_flights — agent + MCP tool.

Multi-provider fan-out:
  1. Fire one supplier search per high-yield airline code AND the complete
     all-airlines pass, all at once.
  2. Wait for every one of them (bounded by _SEARCH_DEADLINE_S) and answer from
     the COMPLETE set. Answering from whichever two providers replied first
     made the quote depend on thread timing: the same question returned
     Rs 1,10,362 at 9.5s and Rs 66,068 at 25s, and once a lone Rs 6,17,259
     Qatar fare while flydubai sold at Rs 84,296.
  3. A provider still running at the deadline merges into flight_cache when
     it lands, so "show more" sees it.
  4. Repeat calls for the same route/dates/passengers/cabin are served from
     flight_cache — zero extra API calls — unless force_refresh is set.

One-way:  pass only departure_date (no return_date).
Round-trip: pass both departure_date and return_date.
"""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from langchain_core.tools import tool

from booking_api import call_flight_search
from core import TripPlannerError
from flight_cache import fare_ref_for, get_entry, set_entry, update_entry
from mcp_tools.server import mcp
from parsers import flight_layovers, flight_leg_duration_min, parse_flight_response
from reference_data_loader import resolve_iata

logger = logging.getLogger(__name__)

# Airline codes that are fast AND carry the inventory Indian travellers want on
# India-Gulf routes, plus the complete "" all-airlines pass. The single-airline
# codes add fares the all-airlines pass omits (AI alone returned 198 AMD-DXB
# fares); the all-airlines pass carries carriers whose own code returns nothing
# (FZ returned 0 on AMD-DXB while flydubai was on sale in the "" pass).
FAST_PROVIDERS: list[str] = ["AI", "6E", "EK", "FZ", "QR", "UL", "SQ"]
BACKFILL_PROVIDERS: list[str] = [""]
_ALL_PROVIDERS: list[str] = [*FAST_PROVIDERS, *BACKFILL_PROVIDERS]

# Upper bound on one search. Measured on AMD-DXB: every provider normally
# answers in 2-9s; this only bites when the supplier stalls.
_SEARCH_DEADLINE_S = 25.0

# The supplier rejects a search over 9 passengers, infants included
# ("Maximum 9 Passengers are allowed" for 8 adults + 1 child + 2 infants).
_MAX_PAX_PER_SEARCH = 9

# Under-2s fly as lap infants (INF), not children.
_INFANT_MAX_AGE = 1


def _search_one_provider(
    airline_code: str,
    origin_iata: str,
    dest_iata: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    children: int,
    child_ages: list[int] | None,
    cabin: str,
    max_stops: int,
    infants: int = 0,
    infant_ages: list[int] | None = None,
) -> tuple[str, list[Any], str]:
    """Call the supplier for one airline code.

    Returns (airline_code, parsed_options, supplier_error). A rejected request
    (success: false) used to look exactly like "no flights"; its description is
    now carried back so the caller can say what actually went wrong.
    """
    try:
        raw = call_flight_search(
            origin_iata=origin_iata,
            destination_iata=dest_iata,
            departure_date=departure_date,
            return_date=return_date,
            adults=adults,
            children=children,
            child_ages=child_ages,
            cabin=cabin,
            max_stops=max_stops,
            airline_code=airline_code,
            infants=infants,
            infant_ages=infant_ages,
        )
        options = parse_flight_response(
            raw,
            expected_origin=origin_iata,
            expected_destination=dest_iata,
            max_results=None,  # parse all, cache will sort
            cabin=cabin,
        )
        return airline_code, options, _supplier_error(raw)
    except Exception as e:
        logger.debug("Provider %s failed: %s", airline_code, e)
        return airline_code, [], ""


def _supplier_error(raw: Any) -> str:
    """The supplier's own rejection message, or "" when the call succeeded."""
    if not isinstance(raw, dict) or raw.get("success") is not False:
        return ""
    errors = raw.get("error") or []
    if isinstance(errors, list):
        texts = [str(e.get("description") or e.get("code") or "")
                 for e in errors if isinstance(e, dict)]
        return "; ".join(t for t in texts if t)
    return str(errors)


def _origin_from_conversation(destination_city: str) -> str:
    """A departure city the customer named, read back from their own words.

    Only returns a city that resolves to an airport AND appears in what the
    customer typed, so it can never invent an origin -- it just recovers one
    the model dropped from the call. The words cover the whole conversation,
    so the most recently named city wins.
    """
    from reference_data_loader import resolve_city
    from rules import _CONVERSATION_TEXT

    said = (_CONVERSATION_TEXT.get() or "").lower()
    if not said:
        return ""

    dest = (destination_city or "").strip().lower()
    for token in reversed(re.findall(r"[a-z]{3,}", said)):
        if token == dest:
            continue
        city = resolve_city(token)
        if not city or not city.get("iata"):
            continue
        if str(city.get("name", "")).strip().lower() == dest:
            continue
        return str(city["name"])
    return ""


def _forward_date(date_str: str) -> str:
    """If an ISO date is in the past, roll its YEAR forward to the next future
    occurrence. Guards against models passing a stale year (e.g. 2023) for a bare
    date like '3 aug', which would make the flight API return zero results.
    Non-ISO / unparseable input is returned unchanged."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date_str
    today = date.today()
    if d >= today:
        return date_str
    # Past date — advance the year until it's today or later.
    y = today.year
    while True:
        try:
            candidate = d.replace(year=y)
        except ValueError:  # e.g. Feb 29 on a non-leap year
            y += 1
            continue
        if candidate >= today:
            return candidate.isoformat()
        y += 1


def _iso(date_str: Any) -> date | None:
    try:
        return datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _shift_years(date_str: str, years: int) -> str:
    d = _iso(date_str)
    if d is None:
        return date_str
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:  # Feb 29 into a non-leap year
        return d.replace(year=d.year + years, day=28).isoformat()


def _forward_trip_dates(
    departure_date: str, return_date: str | None
) -> tuple[str, str | None, dict[str, Any] | None]:
    """Move a past trip to its next occurrence, keeping both dates together.

    Forwarding each date on its own turned "10-16 Oct", asked on 13 Oct, into
    an outbound on 10 Oct 2027 and a return on 16 Oct 2026 -- a return before
    the departure. The return now moves by the same number of years as the
    departure, and the change is reported so the reply can state the year
    instead of quoting next year's fares as this year's.
    """
    forwarded = _forward_date(departure_date)
    if forwarded == departure_date:
        return departure_date, return_date, None
    years = int(forwarded[:4]) - int(departure_date[:4])
    new_return = _shift_years(return_date, years) if return_date else None
    adjusted = {
        "requested": {"departure_date": departure_date, "return_date": return_date},
        "searched": {"departure_date": forwarded, "return_date": new_return},
    }
    return forwarded, new_return, adjusted


def _per_adult(o: Any, searched_pax: int) -> float:
    if o.price_per_adult_inr is not None and o.price_per_adult_inr > 0:
        return round(o.price_per_adult_inr, 2)
    pax = o.pax_count or searched_pax
    return round(o.price_inr / pax, 2) if pax else o.price_inr


def _fmt_hm(minutes: Any) -> str:
    """184 -> '3h 04m'. Empty string when unknown."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return ""
    if m <= 0:
        return ""
    return f"{m // 60}h {m % 60:02d}m"


def _clock(ts: Any) -> str:
    """'2026-09-15 22:25:00' -> '22:25'. Empty string when unparseable."""
    text = str(ts or "").strip()
    if " " in text:
        time_part = text.split(" ", 1)[1]
        if len(time_part) >= 5:
            return time_part[:5]
    return ""


def _day(ts: Any) -> str:
    """'2026-09-15 22:25:00' -> '2026-09-15'. Empty string when unparseable."""
    text = str(ts or "").strip()
    day = text.split(" ", 1)[0].split("T", 1)[0]
    return day if len(day) == 10 and day[4] == "-" else ""


# --- Baggage ------------------------------------------------------------------
# The supplier states baggage two ways, differently per provider: a fare-level
# list (ptC_FareBreakdowns.baggageInfo, one entry per segment on one provider,
# a single entry on the other) and a per-segment luggageInfo. The same FZ 437
# return flight came back "30kg" from one provider and "0kg" from the other.
_QUANTITY = re.compile(r"([1-9]\d*)\s*(kg|kgs|pc|pcs|piece|pieces)\b", re.IGNORECASE)
_ZERO = re.compile(r"0+\s*(kg|kgs|pc|pcs|piece|pieces)?", re.IGNORECASE)


def _bag_text(value: Any) -> str:
    """'30 kg' -> '30kg', '1 piece' / '1PC' -> '1 piece'; '' when no quantity."""
    m = _QUANTITY.search(str(value or ""))
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit.startswith("k"):
        return f"{n}kg"
    return f"{n} piece" if n == 1 else f"{n} pieces"


def _is_zero_bag(value: Any) -> bool:
    return bool(_ZERO.fullmatch(str(value or "").strip()))


def _segment_bags(segment: dict[str, Any], fare_list: list[Any], index: int,
                  total: int, field: str) -> str:
    """One segment's allowance: "30kg", or "" when not confirmed.

    "0kg"/"0pc" is treated as not confirmed rather than "no bag": the supplier
    uses it for unspecified allowances, and telling a customer they get zero
    baggage when we simply do not know is worse than saying so.
    """
    own = segment.get(field)
    if _bag_text(own):
        return _bag_text(own)
    if _is_zero_bag(own):
        return ""
    if len(fare_list) == total:
        return _bag_text(fare_list[index])
    if len(fare_list) == 1:
        return _bag_text(fare_list[0])
    return ""


def _leg_bags(segments: list[dict[str, Any]], offset: int, total: int,
              checked: list[Any], cabin: list[Any]) -> tuple[str, str]:
    """(checked, cabin) allowance for one leg, per flight when they differ."""
    def describe(field: str, fare_list: list[Any]) -> str:
        per_flight = [
            (str(s.get("flight_number") or ""),
             _segment_bags(s, fare_list, offset + i, total, field))
            for i, s in enumerate(segments)
        ]
        values = {v for _, v in per_flight}
        if values == {""}:
            return "not confirmed"
        if len(values) == 1:
            return per_flight[0][1]
        return ", ".join(f"{num} {v or 'not confirmed'}" for num, v in per_flight)

    return describe("luggage_info", checked), describe("cabin_luggage_info", cabin)


def _bag_phrase(allowance: str, kind: str) -> str:
    """'30kg' -> '30kg check-in'; 'not confirmed' -> 'check-in not confirmed';
    a per-flight list -> 'check-in 2546 25kg, 442 30kg'."""
    if allowance == "not confirmed":
        return f"{kind} not confirmed"
    if "," in allowance:
        return f"{kind} {allowance}"
    return f"{allowance} {kind}"


def _baggage_display(checked: Any, cabin: Any) -> str:
    """['25kg'], ['7kg'] -> '25kg check-in + 7kg cabin'.

    The supplier sometimes returns '0pc'/'0kg' meaning "not specified" rather
    than "no allowance"; those are dropped so we never tell a customer they get
    zero baggage when we simply don't know.
    """
    def _clean(vals: Any) -> str:
        if not isinstance(vals, list):
            return ""
        seen: list[str] = []
        for v in vals:
            text = _bag_text(v) or str(v).strip()
            if not text or _is_zero_bag(text) or text.lower() == "none":
                continue
            if text not in seen:
                seen.append(text)
        return " / ".join(seen)

    parts = []
    if bag := _clean(checked):
        parts.append(f"{bag} check-in")
    if cab := _clean(cabin):
        parts.append(f"{cab} cabin")
    return " + ".join(parts)


def _leg_summary(segments: Any) -> dict[str, Any]:
    """Flatten a leg's segments into the few facts a customer actually compares.

    The supplier returns departure/arrival times, terminals, aircraft, seats
    remaining and operating-vs-marketing airline per segment, but the reply only
    ever quoted airline + price. Surfacing these as flat fields means the agent
    can state them without walking the nested structure (and without inventing
    them).
    """
    if not isinstance(segments, list) or not segments:
        return {}
    first, last = segments[0], segments[-1]
    if not isinstance(first, dict) or not isinstance(last, dict):
        return {}

    # A codeshare — sold by one airline, flown by another — matters to customers.
    codeshare = ""
    marketing = str(first.get("marketing_airline") or "").strip()
    operating = str(first.get("operating_airline") or "").strip()
    if marketing and operating and marketing.lower() != operating.lower():
        codeshare = f"{marketing} (operated by {operating})"

    connections = flight_layovers(segments)
    duration = flight_leg_duration_min(segments)

    dep_day, arr_day = _day(first.get("departure")), _day(last.get("arrival"))
    return {
        # Airports and local dates, so a card can show "RPR 13:30 -> DXB 22:05"
        # and a "+1 day" marker without parsing the route string.
        "from_airport": str(first.get("from_airport") or ""),
        "to_airport": str(last.get("to_airport") or ""),
        "from_city": str(first.get("from_city") or ""),
        "to_city": str(last.get("to_city") or ""),
        "departure_date": dep_day,
        "arrival_date": arr_day,
        "next_day": bool(dep_day and arr_day and arr_day > dep_day),
        "departure_time": _clock(first.get("departure")),
        "arrival_time": _clock(last.get("arrival")),
        "departure_terminal": str(first.get("departure_terminal") or ""),
        "arrival_terminal": str(last.get("arrival_terminal") or ""),
        "flight_numbers": [
            str(s.get("flight_number")) for s in segments
            if isinstance(s, dict) and s.get("flight_number")
        ],
        "aircraft": str(first.get("aircraft") or ""),
        "seats_remaining": first.get("seats_remaining"),
        "codeshare": codeshare,
        "stops": max(0, len(segments) - 1),
        "duration_min": duration,
        "duration_display": _fmt_hm(duration),
        "layovers": [f"{airport} {_fmt_hm(minutes)}".strip()
                     for airport, minutes in connections if minutes > 0],
    }


# Fields the model never reads but that cost real context. Measured on a live
# 4-option search: fare_source_code and itinerary_source_code are ~450 tokens
# EACH — 900 of ~1,900 tokens per option — opaque supplier booking blobs. The
# raw segment arrays are another ~500, already summarised into outbound/inbound.
# The raw baggage lists are summarised per leg too; quoting them directly is
# how "0kg" (one provider's unspecified return allowance) reached a customer.
#
# They stay in the flight_cache entry (booking + dedup need them); only the copy
# handed to the LLM is slimmed. get_flight_details resolves `fare_ref` back to
# the full record for the ONE flight a customer picks.
_MODEL_DROP_FIELDS = (
    "fare_source_code",
    "itinerary_source_code",
    "segments_outbound",
    "segments_return",
    "fare_basis_codes",
    "penalties",
    "baggage_info",
    "cabin_baggage_info",
    "conversation_id",
)


def _slim_for_model(option: dict[str, Any]) -> dict[str, Any]:
    """Strip booking-only internals before the option reaches the LLM."""
    slim = {k: v for k, v in option.items() if k not in _MODEL_DROP_FIELDS}
    # A short handle so the agent can refer to a specific fare and
    # get_flight_details can look the full record back up from the cache.
    ref = fare_ref_for(str(option.get("fare_source_code") or ""))
    if ref:
        slim["fare_ref"] = ref
    return slim


def _to_dict(o: Any, searched_pax: int) -> dict[str, Any]:
    d = o.model_dump()
    # price_inr is the whole-party total. Emitting a second key holding that
    # same number (price_total_inr) and a raw-float twin (price_original) gave
    # the model four price fields for two prices, and it quoted the per-adult
    # figure as the total. One name per number.
    d.pop("price_original", None)
    d["price_per_adult_inr"] = _per_adult(o, searched_pax)
    d["pax_count"] = o.pax_count or searched_pax
    d["duration_display"] = _fmt_hm(d.get("duration_min"))
    d["duration_return_display"] = _fmt_hm(d.get("duration_return_min"))

    outbound = d.get("segments_outbound") or []
    inbound = d.get("segments_return") or []
    total = len(outbound) + len(inbound)
    checked = list(d.get("baggage_info") or [])
    cabin = list(d.get("cabin_baggage_info") or [])

    d["outbound"] = _leg_summary(outbound)
    if d["outbound"]:
        d["outbound"]["checked_baggage"], d["outbound"]["cabin_baggage"] = _leg_bags(
            outbound, 0, total, checked, cabin)
    inbound_summary = _leg_summary(inbound)
    if inbound_summary:
        inbound_summary["checked_baggage"], inbound_summary["cabin_baggage"] = _leg_bags(
            inbound, len(outbound), total, checked, cabin)
        d["inbound"] = inbound_summary

    # Baggage is a deciding fact for Indian leisure travellers, not a
    # "breakdown" — surface it flat so the reply can quote it without walking
    # the nested fare structure. Per leg when the legs differ.
    legs = [leg for leg in (d.get("outbound"), d.get("inbound")) if leg]
    per_leg = [f"{_bag_phrase(leg['checked_baggage'], 'check-in')} + "
               f"{_bag_phrase(leg['cabin_baggage'], 'cabin')}" for leg in legs]
    if per_leg and len(set(per_leg)) == 1:
        d["baggage_display"] = per_leg[0]
    elif per_leg:
        d["baggage_display"] = f"Outbound: {per_leg[0]} · Return: {per_leg[1]}"
    else:
        d["baggage_display"] = _baggage_display(checked, cabin)
    return d


def _itinerary_key(option: dict[str, Any]) -> tuple:
    """Identity of the flights themselves, independent of who sells them.

    Two suppliers sell the same flights at different prices (FZ 438/437 came
    back at Rs 84,942 and Rs 92,634). Listing both put "the same flight twice"
    in front of the customer, and the model then insisted they were different
    itineraries.
    """
    legs = []
    for name in ("segments_outbound", "segments_return"):
        legs.append(tuple(
            (str(s.get("marketing_airline_code") or ""), str(s.get("flight_number") or ""),
             str(s.get("departure") or ""))
            for s in option.get(name) or [] if isinstance(s, dict)
        ))
    if not any(legs):
        return ("price", option.get("airline", ""), round(float(option.get("price_inr") or 0)))
    return tuple(legs)


@dataclass(frozen=True)
class _Route:
    origin_iata: str
    dest_iata: str
    departure_date: str
    return_date: str | None
    cabin: str
    max_stops: int


@dataclass(frozen=True)
class _Party:
    adults: int
    children: int
    child_ages: tuple[int, ...] | list[int]
    infants: int
    infant_ages: tuple[int, ...] | list[int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "child_ages", tuple(self.child_ages))
        object.__setattr__(self, "infant_ages", tuple(self.infant_ages))

    @property
    def seats(self) -> int:
        """Passengers the supplier counts against its limit (infants included)."""
        return self.adults + self.children + self.infants

    @property
    def seated(self) -> int:
        """Passengers who need a seat of their own (infants sit on a lap)."""
        return self.adults + self.children

    def params(self) -> dict[str, int]:
        return {"adults": self.adults, "children": self.children, "infants": self.infants}


# Larger parties are a group booking for the agency, not a chat search.
_MAX_BOOKINGS_PER_SEARCH = 4


def _split_party(party: _Party) -> list[_Party]:
    """The party as the fewest bookings the supplier accepts, sized evenly.

    Each booking has at most _MAX_PAX_PER_SEARCH passengers and never more lap
    infants than adults. 10 adults -> 5 + 5, never 9 + 1: equal bookings are
    priced by one search instead of two.
    """
    n = -(-party.seats // _MAX_PAX_PER_SEARCH)
    if n <= 1:
        return [party]
    adults = [party.adults // n + (1 if i < party.adults % n else 0) for i in range(n)]
    infants: list[list[int]] = [[] for _ in range(n)]
    for k in range(party.infants):
        age = party.infant_ages[k] if k < len(party.infant_ages) else 1
        room = [i for i in range(n) if len(infants[i]) < adults[i]]
        infants[min(room, key=lambda i: (adults[i] + len(infants[i]), i))].append(age)
    children: list[list[int | None]] = [[] for _ in range(n)]
    for k in range(party.children):
        age = party.child_ages[k] if k < len(party.child_ages) else None
        size = [adults[i] + len(infants[i]) + len(children[i]) for i in range(n)]
        children[min(range(n), key=lambda i: (size[i], i))].append(age)
    return [
        _Party(adults[i], len(children[i]), [a for a in children[i] if a is not None],
               len(infants[i]), infants[i])
        for i in range(n)
    ]


@dataclass
class _Pool:
    options: list[dict[str, Any]]
    providers_done: list[str]
    providers_pending: list[str]
    errors: list[str]
    from_cache: bool
    search_params: dict[str, Any]


def _search_pool(route: _Route, party: _Party, search_params: dict[str, Any], *,
                 force_refresh: bool) -> _Pool:
    """Every provider's fares for one booking, from the cache or a fresh fan-out."""
    key_args = (route.origin_iata, route.dest_iata, route.departure_date, route.return_date,
                party.adults, party.children)
    key_kw = {"infants": party.infants, "cabin": route.cabin, "max_stops": route.max_stops}

    cached = None if force_refresh else get_entry(*key_args, **key_kw)
    if cached is not None:
        return _Pool(cached.all_options, cached.providers_done, cached.providers_pending,
                     [], True, cached.search_params)

    all_options: list[dict[str, Any]] = []
    seen_fares: set[str] = set()
    done: list[str] = []
    errors: list[str] = []
    lock = threading.Lock()

    set_entry(*key_args, options=[], providers_done=[],
              providers_pending=list(_ALL_PROVIDERS), search_params=search_params, **key_kw)

    def _merge(code: str, opts: list[Any], error: str) -> None:
        with lock:
            done.append(code)
            if error:
                errors.append(error)
            for o in opts:
                d = _to_dict(o, party.seats)
                fsc = d.get("fare_source_code")
                if fsc not in seen_fares:
                    seen_fares.add(fsc)
                    all_options.append(d)
            all_options.sort(key=lambda o: o.get("price_inr", float("inf")))
            pending = [c for c in _ALL_PROVIDERS if c not in done]
            update_entry(*key_args, new_options=list(all_options),
                         providers_done=list(done), providers_pending=pending, **key_kw)

    pool = ThreadPoolExecutor(max_workers=len(_ALL_PROVIDERS))
    futures = [
        pool.submit(_search_one_provider, c, route.origin_iata, route.dest_iata,
                    route.departure_date, route.return_date, party.adults, party.children,
                    list(party.child_ages) or None, route.cabin, route.max_stops,
                    party.infants, list(party.infant_ages))
        for c in _ALL_PROVIDERS
    ]
    finished_in_time, late = wait(futures, timeout=_SEARCH_DEADLINE_S, return_when=ALL_COMPLETED)
    for f in finished_in_time:
        _merge(*f.result())
    for f in late:
        # Lands after we answer but still reaches the cache for "show more".
        # If it completed a moment ago this runs immediately, here, once.
        f.add_done_callback(lambda fut: _merge(*fut.result()))
    pool.shutdown(wait=False)

    with lock:
        return _Pool(
            list(all_options), list(done),
            [c or "all-airlines" for c in _ALL_PROVIDERS if c not in done],
            sorted(set(errors)), False, search_params,
        )


def _seats_left(option: dict[str, Any]) -> int | None:
    """Fewest seats the supplier says remain on any flight of the trip."""
    counts = [s.get("seats_remaining") for name in ("segments_outbound", "segments_return")
              for s in option.get(name) or [] if isinstance(s, dict)]
    counts = [int(c) for c in counts if isinstance(c, int | float) and c >= 0]
    return min(counts) if counts else None


def _combine_bookings(bookings: list[_Party], pools: list[_Pool]) -> list[dict[str, Any]]:
    """Whole-party options: itineraries every booking can buy, priced as a sum.

    An itinerary is offered only if each booking's own search returned it and
    the supplier shows enough seats for everyone who needs one.
    """
    cheapest: list[dict[tuple, dict[str, Any]]] = []
    for pool in pools:
        by_key: dict[tuple, dict[str, Any]] = {}
        for o in pool.options:  # price-sorted
            by_key.setdefault(_itinerary_key(o), o)
        cheapest.append(by_key)

    seated = sum(b.seated for b in bookings)
    adults = sum(b.adults for b in bookings)
    combined: list[dict[str, Any]] = []
    for key, first in cheapest[0].items():
        parts = [c.get(key) for c in cheapest]
        if any(p is None for p in parts):
            continue
        # The supplier reports at most 9 (2,979 of 4,187 live segments read 9),
        # so 9 means "9 or more"; only a lower figure is a real limit.
        seats = _seats_left(first)
        if seats is not None and seats < _MAX_PAX_PER_SEARCH and seats < seated:
            continue
        row = dict(first)
        for money in ("price_inr", "base_fare_inr", "total_tax_inr"):
            if all(isinstance(p.get(money), int | float) for p in parts):
                row[money] = round(sum(float(p[money]) for p in parts), 2)
        row["price_per_adult_inr"] = round(sum(
            float(p.get("price_per_adult_inr") or 0) * b.adults
            for p, b in zip(parts, bookings)) / max(1, adults), 2)
        row["pax_count"] = sum(b.seats for b in bookings)
        row["refundable"] = all(bool(p.get("refundable")) for p in parts)
        row["bookings"] = [
            {"priced_for": _priced_for(b.params()), "price_inr": p["price_inr"],
             "refundable": bool(p.get("refundable")),
             "fare_ref": fare_ref_for(str(p.get("fare_source_code") or ""))}
            for p, b in zip(parts, bookings)
        ]
        combined.append(row)
    combined.sort(key=lambda o: o["price_inr"])
    return combined


def _validate_party(adults: int, children: int, infants: int) -> dict[str, Any] | None:
    """A refusal the model can act on, or None when the party can be searched."""
    seats = adults + children + infants
    if seats > _MAX_PAX_PER_SEARCH * _MAX_BOOKINGS_PER_SEARCH:
        return {
            "error": True,
            "error_type": "GroupBookingRequired",
            "message": (
                f"A party of {seats} is a group booking; chat search covers up to "
                f"{_MAX_PAX_PER_SEARCH * _MAX_BOOKINGS_PER_SEARCH} passengers."
            ),
            "agent_instructions": (
                "Tell the customer a group this size is handled by our group desk "
                "and offer to connect them. Do not quote any fare."
            ),
        }
    if infants > adults:
        return {
            "error": True,
            "error_type": "TooManyInfants",
            "message": "Each lap infant (under 2) must travel with their own adult.",
            "agent_instructions": (
                "Ask the customer to confirm the ages: airlines allow one infant per "
                "adult. Do not search until the party is valid."
            ),
        }
    return None


def _impl(
    origin_city: str,
    destination_city: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    child_ages: list[int] | None = None,
    cabin: str = "Y",
    max_stops: int = 2,
    max_results: int = 5,
    airline_filter: str = "",
    force_refresh: bool = False,
    assume_missing: bool = False,
    infants: int = 0,
) -> dict[str, Any]:
    """Search for flights across all airline providers simultaneously.

    Args:
        origin_city:     The departure city THE CUSTOMER NAMED. Never infer or
                         default one — if they have not said where they are
                         flying from, pass "" and ask them. Passing a plausible
                         city (Mumbai) for a customer who never named one showed
                         invented fares as fact.
        destination_city: Destination (typically "Dubai").
        departure_date:  ISO yyyy-mm-dd — outbound date.
        return_date:     ISO yyyy-mm-dd — return date for round-trip. Omit for one-way.
        adults:          Adult passenger count.
        children:        Child passenger count (ages 2-11).
        child_ages:      Age of each child (length must equal children). A child
                         under 2 is searched as a lap infant automatically.
        cabin:           'Y' economy / 'S' premium economy / 'C' business / 'F' first.
                         Results never include a cabin below the one asked for.
        max_stops:       Max layovers (default 2).
        max_results:     Options to return (default 5). When the customer asks to
                         "see more" / "show 10 options", CALL THIS TOOL AGAIN with a
                         higher max_results (e.g. 10 or 15) — the cache already holds
                         hundreds of options across all airlines, so a second call is
                         instant and returns MORE variety (different airlines/prices),
                         NOT the same 5. Never tell the customer "that's all" without
                         re-calling with a higher max_results first.
        airline_filter:  IATA code to filter results from cache (e.g. "EK" for Emirates).
                         Matches any flight on the trip. Only filters display —
                         does not re-search.
        force_refresh:   True for "check again" / "is it still that price" /
                         right before booking: ignores every cache and asks the
                         supplier again.
        infants:         Lap infants under 2 (one per adult).

    Returns:
        {options, cheapest_price_inr, cheapest_price_per_adult_inr, total_results,
         cached_total, providers_done, providers_still_loading, search_complete,
         trip_type, cabin, search_params}
        Each option carries outbound/inbound legs with times, duration (layovers
        included), stops, layovers and baggage, plus cabin and refundable.
        Or {error: True, message: ...} on failure.

    TRIP TYPE NOTE:
        One-way:    pass departure_date only (no return_date)
        Round-trip: pass both departure_date AND return_date
        assume_missing: only True when the customer was asked for a mandatory
            field and declined or told you to search anyway. Never set it to
            skip asking.
    """
    from rules import customer_said, missing_search_fields, needs_input_error

    # A departure city the customer never named is treated as missing: the model
    # fills the gap with a plausible one and a trip with no stated origin was
    # quoted BOM->DXB fares as fact.
    stated_origin = origin_city if customer_said(origin_city) else ""
    if not stated_origin:
        # The customer named a city and the model dropped it from the call.
        # Asked for "flights from hyd to dubai" it sent destination and date
        # but no origin, got the refusal, and answered it with four invented
        # airlines and fares. Recovering the city from their own words keeps
        # the anti-invention guard (the value still has to be something they
        # said) while not costing them a turn for data they already gave.
        stated_origin = _origin_from_conversation(destination_city)
        if stated_origin:
            origin_city = stated_origin

    missing = missing_search_fields(
        origin_city=stated_origin, departure_date=departure_date, adults=adults
    )
    # Airlines price by age: an under-2 is a lap infant (~Rs 800-3,200), a
    # 2-11 is a child (~Rs 30,000+). Told "our 1 year old baby", the model
    # passed children=1 with no age and the baby was quoted a child fare.
    if children and len(child_ages or []) != children:
        missing.append("child_ages")
    if missing and not assume_missing:
        return needs_input_error(missing)

    # Under-2s travel on an adult's lap and are priced as INF. Sent as CHD a
    # baby was charged a full child fare (~Rs 30,577 vs ~Rs 800-3,200).
    ages = [int(a) for a in (child_ages or [])]
    infant_ages = [a for a in ages if a <= _INFANT_MAX_AGE]
    if infant_ages:
        ages = [a for a in ages if a > _INFANT_MAX_AGE]
        children = max(0, children - len(infant_ages))
        infants += len(infant_ages)
    child_ages = ages or None
    cabin = normalize_cabin(cabin)

    refusal = _validate_party(adults, children, infants)
    if refusal:
        return refusal

    try:
        # Small models sometimes pass a PAST date (e.g. llama defaulting "3 aug"
        # to 2023) and the supplier returns 0 flights. The trip moves to its
        # next occurrence as one unit, and the move is reported.
        departure_date, return_date, dates_adjusted = _forward_trip_dates(
            departure_date, return_date)
        out_day, back_day = _iso(departure_date), _iso(return_date)
        if out_day and back_day and back_day < out_day:
            return {
                "error": True,
                "error_type": "InvalidDates",
                "message": f"The return date {return_date} is before the departure date {departure_date}.",
                "agent_instructions": "Ask the customer to confirm both travel dates.",
            }

        origin_iata = resolve_iata(origin_city)
        dest_iata = resolve_iata(destination_city)
        if not origin_iata:
            return {"error": True, "message": f"Unknown origin city: {origin_city!r}", "error_type": "UnsupportedRoute"}
        if not dest_iata:
            return {"error": True, "message": f"Unknown destination city: {destination_city!r}", "error_type": "UnsupportedRoute"}

        trip_type = "round_trip" if return_date else "one_way"
        route = _Route(origin_iata, dest_iata, departure_date, return_date, cabin, max_stops)
        party = _Party(adults, children, list(child_ages or []), infants, list(infant_ages))
        search_params = {
            "origin": origin_city, "origin_iata": origin_iata,
            "destination": destination_city, "destination_iata": dest_iata,
            "departure_date": departure_date, "return_date": return_date,
            "adults": adults, "children": children, "infants": infants,
            "cabin": cabin, "pax_count": party.seats, "trip_type": trip_type,
        }

        bookings = _split_party(party)
        if len(bookings) == 1:
            found = _search_pool(route, party, search_params, force_refresh=force_refresh)
            return _answer(
                found.options, max_results=max_results, airline_filter=airline_filter,
                cabin=cabin, trip_type=trip_type, from_cache=found.from_cache,
                providers_done=found.providers_done, providers_pending=found.providers_pending,
                search_params=found.search_params, dates_adjusted=dates_adjusted,
                supplier_errors=found.errors,
            )

        # Over the supplier's 9-passenger limit: search the party as balanced
        # bookings (10 -> 5 + 5) in parallel and quote whole-party totals. Left
        # to the model, a 10-person request ended on "One moment while I pull
        # those options", and the nudge to finish produced invented flights.
        distinct = list(dict.fromkeys(bookings))
        with ThreadPoolExecutor(max_workers=len(distinct)) as ex:
            pools = dict(zip(distinct, ex.map(
                lambda b: _search_pool(route, b, {**search_params, **b.params()},
                                       force_refresh=force_refresh),
                distinct)))
        found = [pools[b] for b in bookings]
        search_params = {**search_params,
                         "bookings": [_priced_for(b.params()) for b in bookings]}
        return _answer(
            _combine_bookings(bookings, found), max_results=max_results,
            airline_filter=airline_filter, cabin=cabin, trip_type=trip_type,
            from_cache=all(f.from_cache for f in found),
            providers_done=sorted({c for f in found for c in f.providers_done}),
            providers_pending=sorted({c for f in found for c in f.providers_pending}),
            search_params=search_params, dates_adjusted=dates_adjusted,
            supplier_errors=sorted({e for f in found for e in f.errors}),
        )

    except TripPlannerError as e:
        logger.warning("search_flights error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("search_flights unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


_CABIN_NAMES = {"Y": "economy", "S": "premium economy", "C": "business", "F": "first"}


def normalize_cabin(value: Any) -> str:
    """Any way a cabin is written -> the supplier's one-letter code.

    The supplier answers an unknown cabin with zero fares and no error, so a
    model that passed "'Y'" (quotes included) or "economy" got "no flights
    from Ahmedabad" for a route with five. Unknown input falls back to
    economy rather than to an empty search.
    """
    text = str(value or "").strip().strip("'\"`").strip().lower()
    if not text:
        return "Y"
    if text.upper() in _CABIN_NAMES:
        return text.upper()
    if "premium" in text:
        return "S"
    if "business" in text or text in {"j"}:
        return "C"
    if "first" in text:
        return "F"
    return "Y"


def _priced_for(search_params: dict[str, Any]) -> str:
    """'2 adults + 1 infant' — the party these fares were actually priced for.

    Told about "our 1 year old baby", the model searched 2 adults and labelled
    the 2-adult fare "2 Adults + 1 Infant". The label now comes from the search.
    """
    parts = []
    for key, one, many in (("adults", "adult", "adults"), ("children", "child", "children"),
                           ("infants", "infant", "infants")):
        n = int(search_params.get(key) or 0)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    label = " + ".join(parts)
    bookings = search_params.get("bookings") or []
    if len(bookings) > 1:
        label += f", as {len(bookings)} bookings ({'; '.join(bookings)})"
    return label


def _answer(
    pool: list[dict[str, Any]],
    *,
    max_results: int,
    airline_filter: str,
    cabin: str,
    trip_type: str,
    from_cache: bool,
    providers_done: list[str],
    providers_pending: list[str],
    search_params: dict[str, Any],
    dates_adjusted: dict[str, Any] | None,
    supplier_errors: list[str],
) -> dict[str, Any]:
    """The tool result, built the same way for a fresh search and a cache hit.

    The cache path used to skip the slimming, so a repeat question handed the
    model ~900 extra tokens of booking codes per option.
    """
    if airline_filter:
        code = airline_filter.upper()
        pool = [o for o in pool if _matches_airline(o, code)]
    display = [_slim_for_model(o) for o in _diversify(pool, max_results)]
    cheapest = display[0] if display else None
    cabin_name = _CABIN_NAMES.get(cabin, cabin)

    if display:
        instructions = (
            "These flight results are READY. Present them to the customer "
            "now in your reply. DO NOT call search_flights again — you "
            "already have the results. Every row comes from that option alone: "
            "airline, outbound and inbound times with each leg's "
            "duration_display, stops and layovers, baggage_display, cabin, "
            "refundable, and price_inr labelled with priced_for. Durations "
            "already include time zones and layovers; never work one out from "
            "clock times. Refundable is per fare; never say a whole list is "
            "refundable. Baggage 'not confirmed' means exactly that. Refund and "
            "change terms come only from get_flight_details penalties."
        )
    elif supplier_errors:
        instructions = (
            "The airline system rejected this search. Tell the customer what it "
            "said and what to change. Do not describe any flight or fare."
        )
    else:
        instructions = (
            f"No {cabin_name} fares were found for these dates. Say exactly that. "
            "Do not offer or describe fares from another cabin as if they were "
            f"{cabin_name}, and do not invent flights."
        )
    if providers_pending:
        instructions += (
            " Some airlines were still answering when these results were taken; "
            "calling search_flights again shortly returns the complete list."
        )
    if dates_adjusted:
        searched = dates_adjusted["searched"]
        instructions += (
            f" The dates given had already passed, so this searched "
            f"{searched['departure_date']}"
            + (f" to {searched['return_date']}" if searched.get("return_date") else "")
            + ". State those dates, with the year, before any fare."
        )

    result: dict[str, Any] = {
        "options": display,
        "cheapest_price_inr": cheapest["price_inr"] if cheapest else None,
        "cheapest_price_per_adult_inr": cheapest.get("price_per_adult_inr") if cheapest else None,
        "total_results": len(display),
        "cached_total": len(pool),
        "providers_done": list(providers_done),
        "providers_still_loading": list(providers_pending),
        "search_complete": not providers_pending,
        "from_cache": from_cache,
        "trip_type": trip_type,
        "cabin": cabin,
        "priced_for": _priced_for(search_params),
        "pricing_note": (
            "price_inr is the whole-party total for exactly `priced_for`; "
            "price_per_adult_inr is one adult. Quote price_inr as the trip price "
            "and label it with `priced_for` — if that is not the customer's whole "
            "party, search again with the right adults/children/child_ages/infants."
        ),
        # STOP signal — without it the model often re-calls search_flights,
        # and that extra LLM round truncated the streamed reply mid-sentence
        # (seen as "Quick question: How" cut off).
        "agent_instructions": instructions,
        "search_params": search_params,
    }
    if dates_adjusted:
        result["dates_adjusted"] = dates_adjusted
    if supplier_errors and not display:
        result["supplier_errors"] = supplier_errors
    return result


def _diversify(options: list[dict[str, Any]], limit: int, per_airline: int | None = None) -> list[dict[str, Any]]:
    """Pick up to `limit` options showing airline VARIETY (price-sorted input).

    Two passes:
      1. One row per itinerary — the same flights sold by two suppliers keep
         only the cheaper fare. (The old key was airline + rounded price,
         which merged DIFFERENT flights that cost the same and kept the SAME
         flights when two suppliers priced them differently.)
      2. Cap each airline to `per_airline` in the shown set so one cheap airline
         can't monopolise all slots; backfill remaining slots if we run short.

    per_airline scales with the request: a small list (5) caps at 2/airline for
    variety; a "show me 10" list allows more per airline so we can actually fill
    the slots instead of running short.
    """
    if per_airline is None:
        per_airline = 2 if limit <= 6 else max(3, limit // 3)

    seen: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for o in options:  # already price-sorted, so the first of each is cheapest
        k = _itinerary_key(o)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(o)

    picked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    overflow: list[dict[str, Any]] = []
    for o in deduped:
        a = o.get("airline", "")
        if counts.get(a, 0) < per_airline:
            picked.append(o)
            counts[a] = counts.get(a, 0) + 1
        else:
            overflow.append(o)
        if len(picked) >= limit:
            break
    # If diversity capping left us short of `limit`, backfill from overflow.
    if len(picked) < limit:
        picked.extend(overflow[: limit - len(picked)])
    return picked[:limit]


def _matches_airline(option: dict[str, Any], iata_code: str) -> bool:
    """True if any flight on the trip is marketed or operated by `iata_code`.

    Checking only the first segment hid AI 2546 + FZ 442 from a flydubai filter.
    """
    if str(option.get("airline_code", "")).upper() == iata_code:
        return True
    for name in ("segments_outbound", "segments_return", "segments"):
        for s in option.get(name) or []:
            if not isinstance(s, dict):
                continue
            codes = {str(s.get("marketing_airline_code") or "").upper(),
                     str(s.get("operating_airline_code") or "").upper()}
            if iata_code in codes:
                return True
    return False


# Cached like every other availability tool. Without this the supplier's
# non-determinism leaked straight to the customer: the fare quoted in chat had
# already changed by the time the itinerary was priced. `force_refresh=True`
# bypasses this cache AND flight_cache for a deliberate "check again".
from mcp_tools.result_cache import cache_impl

search_flights_tool = tool(cache_impl("search_flights")(_impl))
search_flights_tool.name = "search_flights"
mcp.tool(name="search_flights")(_impl)
