"""scheduling — pure time-feasibility logic for itineraries.

No I/O, no API calls, no LLM. Input: arrival time, tours (with or without
timeslots), policy. Output: a day-by-day plan plus an explicit list of what
could NOT be placed and why.

Why this exists as code and not as prompt rules: `build_trip_schedule_tool`
accepts `start`/`end` as free-text and validates nothing, so every time in a
rendered schedule today is invented by the model. A rest-gap rule in a prompt
cannot be tested; the same rule here gets a unit test.

The motivating failure: a flight lands 17:00, the customer reaches the hotel by
17:30, and the last Burj Khalifa slot is 18:00. With a 3-hour rest policy the
customer is not free until 20:30, so that tour must move to the next day — and
the reason must be quotable back to them, not silently dropped.

Real-data constraints this module is built around (all verified live):
  - 25% of tours return a placeholder "00:00" slot. Trusting it would tell a
    customer their tour starts at midnight.
  - 41% of tours have no slots at all and must schedule by duration.
  - `duration` arrives in at least three formats and is empty on 141/332 tours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

# =============================================================================
# Policy
# =============================================================================
# Rest after landing before the first activity. Product decision: 3 hours, and
# NO inter-activity gap — activities may run back-to-back once rested.
DEFAULT_REST_AFTER_LANDING_MIN = 180
# Airport -> hotel allowance when the caller doesn't give a hotel arrival time.
DEFAULT_AIRPORT_TO_HOTEL_MIN = 60
# Flat allowance for getting to a tour's start point.
DEFAULT_TRANSIT_TO_ACTIVITY_MIN = 45
# Assumed length of a tour whose `duration` the supplier left empty.
DEFAULT_ACTIVITY_MIN = 120
# Nothing is scheduled to start after this — a 23:00 slot is real, but starting
# a 4-hour tour then is not something we'd propose.
LATEST_ACTIVITY_START = time(21, 0)
# And nothing should FINISH later than this. Guarding only the start let a
# 5-hour tour begin 18:30 and end 23:30.
LATEST_ACTIVITY_END = time(23, 0)


@dataclass(frozen=True)
class SchedulePolicy:
    """All tunables in one place so tests can pin them explicitly."""

    rest_after_landing_min: int = DEFAULT_REST_AFTER_LANDING_MIN
    airport_to_hotel_min: int = DEFAULT_AIRPORT_TO_HOTEL_MIN
    transit_to_activity_min: int = DEFAULT_TRANSIT_TO_ACTIVITY_MIN
    default_activity_min: int = DEFAULT_ACTIVITY_MIN
    latest_activity_start: time = LATEST_ACTIVITY_START
    latest_activity_end: time = LATEST_ACTIVITY_END
    day_start: time = time(9, 0)
    # Target activity MINUTES per day, not a tour count. Counting tours meant a
    # 1-hour kayak "used up" a day exactly like an 8-hour Abu Dhabi trip, so a
    # 5-night trip got five short tours and five near-empty days. Roughly 7
    # hours of sightseeing is a full but humane day.
    target_activity_min_per_day: int = 420
    # Even short activities need travel, queuing and a meal between them. Four
    # in a day is busy; more reads as a conveyor belt. Guarding only on minutes
    # let six 30-minute tickets stack onto one day.
    max_activities_per_day: int = 4


# =============================================================================
# Slot + duration parsing
# =============================================================================
# A placeholder the supplier uses for "no fixed time" — NOT midnight.
_PLACEHOLDER_SLOTS = {"00:00", "0:00", "00:00:00", ""}


def parse_slot_time(raw: Any) -> time | None:
    """"07:30" -> time(7, 30). None for placeholders and junk.

    Returning None for "00:00" is deliberate: 25% of tours use it to mean "no
    fixed time", and treating it as midnight would schedule a tour at 00:00.
    """
    text = str(raw or "").strip()
    if text in _PLACEHOLDER_SLOTS:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    if hh == 0 and mm == 0:
        return None  # placeholder in another skin
    return time(hh, mm)


def usable_slots(slots: list[dict[str, Any]] | None) -> list[tuple[time, dict[str, Any]]]:
    """Real, bookable slots sorted by time.

    Drops placeholders and anything with no remaining availability. Returns the
    raw row alongside the parsed time so callers keep `timeSlotId` for booking.
    """
    out: list[tuple[time, dict[str, Any]]] = []
    for s in slots or []:
        if not isinstance(s, dict):
            continue
        t = parse_slot_time(s.get("timeSlot") or s.get("time_slot"))
        if t is None:
            continue
        avail = s.get("available")
        if isinstance(avail, (int, float)) and avail <= 0:
            continue  # sold out
        out.append((t, s))
    out.sort(key=lambda x: x[0])
    return out


def parse_duration_minutes(raw: Any) -> int | None:
    """Minutes from the supplier's several duration formats. None if unknown.

    Handles all shapes seen in live data:
        "0-Days 2-Hours 30-Minutes"   -> 150
        "4 Hours (Approx)"            -> 240
        "15 Minutes (Approx)"         -> 15
        "Dubai Beach - 30 / 60 mins"  -> 30   (first number wins)
        ""                            -> None
    """
    text = str(raw or "").strip().lower()
    if not text:
        return None

    # A range ("30 / 60 mins", "2-3 hours") — quote the SHORTER duration so we
    # never over-book the day on an optimistic reading.
    rng = re.search(r"(\d+)\s*(?:/|-|to)\s*(\d+)\s*(min|hour|hr)", text)
    if rng:
        low = min(int(rng.group(1)), int(rng.group(2)))
        return low * 60 if rng.group(3).startswith(("hour", "hr")) else low

    # Structured "N-Days N-Hours N-Minutes"
    d = re.search(r"(\d+)\s*-?\s*days?", text)
    h = re.search(r"(\d+)\s*-?\s*hours?", text)
    mi = re.search(r"(\d+)\s*-?\s*min", text)
    if d or h or mi:
        total = 0
        if d:
            total += int(d.group(1)) * 24 * 60
        if h:
            total += int(h.group(1)) * 60
        if mi:
            total += int(mi.group(1))
        return total or None

    # Bare number, no unit — assume minutes.
    n = re.search(r"(\d+)", text)
    return int(n.group(1)) if n else None


# =============================================================================
# Time helpers
# =============================================================================
def _to_time(raw: Any) -> time | None:
    """Accept "17:00", "17:00:00", a time, or a datetime."""
    if isinstance(raw, time):
        return raw
    if isinstance(raw, datetime):
        return raw.time()
    text = str(raw or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return time(hh, mm) if 0 <= hh <= 23 and 0 <= mm <= 59 else None


def _add_minutes(t: time, minutes: int) -> tuple[time, int]:
    """Add minutes to a clock time. Returns (time, days_carried)."""
    total = t.hour * 60 + t.minute + minutes
    carry, rem = divmod(total, 24 * 60)
    return time(rem // 60, rem % 60), carry


def _fmt(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


# =============================================================================
# Results
# =============================================================================
@dataclass
class ScheduledItem:
    start: str
    end: str
    title: str
    kind: str  # flight|transfer|hotel|tour|restaurant|activity|free
    detail: str = ""
    slot_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        d = {"start": self.start, "end": self.end, "title": self.title,
             "kind": self.kind, "detail": self.detail}
        if self.slot_id:
            d["slot_id"] = self.slot_id
        return d


@dataclass
class ExcludedItem:
    """A tour that could NOT be placed, and the customer-quotable reason."""

    title: str
    reason: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "reason": self.reason, "detail": self.detail}


@dataclass
class DayPlan:
    day_number: int
    date: str
    label: str = ""
    items: list[ScheduledItem] = field(default_factory=list)

    @property
    def title(self) -> str:
        """"Day 1 — 2026-09-15 (Arrival)". Named `title` because the PDF reads
        exactly that key (`itinerary_pdf.py` -> `d.get("title")`); emitting only
        `label`/`date` left every day heading in the PDF blank."""
        head = f"Day {self.day_number} — {self.date}" if self.date else f"Day {self.day_number}"
        return f"{head} ({self.label})" if self.label else head

    def as_dict(self) -> dict[str, Any]:
        return {"day_number": self.day_number, "date": self.date, "label": self.label,
                "title": self.title, "items": [i.as_dict() for i in self.items]}


# =============================================================================
# The scheduler
# =============================================================================
def earliest_free_time(
    *,
    arrival_time: Any,
    hotel_checkin_time: Any = None,
    policy: SchedulePolicy | None = None,
) -> time | None:
    """When the customer is first available for an activity on arrival day.

    landing + airport→hotel transit + rest. If the caller already knows when
    they reach the hotel, that replaces the transit estimate.
    """
    p = policy or SchedulePolicy()
    at_hotel = _to_time(hotel_checkin_time)
    if at_hotel is None:
        landed = _to_time(arrival_time)
        if landed is None:
            return None
        at_hotel, _ = _add_minutes(landed, p.airport_to_hotel_min)
    free, carry = _add_minutes(at_hotel, p.rest_after_landing_min)
    return None if carry else free  # rolled past midnight -> nothing today


def _activity_minutes(tour: dict[str, Any], p: SchedulePolicy) -> int:
    return parse_duration_minutes(tour.get("duration")) or p.default_activity_min


def _place_on_day(
    tour: dict[str, Any],
    *,
    not_before: time,
    policy: SchedulePolicy,
) -> tuple[ScheduledItem | None, str]:
    """Try to place one tour after `not_before`. Returns (item, reason_if_failed).

    Slot-based tours must use a real published slot; others start as soon as the
    customer can get there.
    """
    title = str(tour.get("name") or tour.get("title") or "Tour").strip()
    dur = _activity_minutes(tour, policy)
    slots = usable_slots(tour.get("timeslots") or tour.get("slots"))
    ready, carry = _add_minutes(not_before, policy.transit_to_activity_min)
    if carry:
        return None, "no time left on this day"

    if slots:
        for slot_t, row in slots:
            if slot_t < ready:
                continue
            end, carry = _add_minutes(slot_t, dur)
            if carry:
                continue  # this slot would run past midnight; try a later-listed one
            # The FINISH time is the real constraint. A blanket latest-start cap
            # wrongly blocked a 30-minute 21:45 fountain show that ends 22:15.
            if end > policy.latest_activity_end:
                continue
            return (
                ScheduledItem(
                    start=_fmt(slot_t), end=_fmt(end), title=title, kind="tour",
                    detail=str(tour.get("detail") or ""),
                    slot_id=str(row.get("timeSlotId") or row.get("slot_id") or ""),
                ),
                "",
            )
        # Every slot is before the customer is free — the Burj case.
        last = _fmt(slots[-1][0])
        return None, (
            f"last slot is {last}, but you are not free until {_fmt(ready)}"
        )

    # No published slots: schedule by duration.
    if ready > policy.latest_activity_start:
        return None, f"too late to start ({_fmt(ready)})"
    end, carry = _add_minutes(ready, dur)
    if carry:
        return None, "would run past midnight"
    # A long tour must also FINISH at a civil hour. Guarding only the start time
    # let a 5-hour tour begin at 18:30 and run to 23:30, which is not something
    # we would ever propose to a customer.
    if end > policy.latest_activity_end:
        return None, (
            f"a {dur // 60}h tour starting {_fmt(ready)} would not finish "
            f"until {_fmt(end)}"
        )
    return (
        ScheduledItem(start=_fmt(ready), end=_fmt(end), title=title, kind="tour",
                      detail=str(tour.get("detail") or "")),
        "",
    )


def build_itinerary(
    *,
    start_date: str,
    nights: int,
    tours: list[dict[str, Any]] | None = None,
    arrival_time: Any = None,
    hotel_name: str = "",
    hotel_checkin_time: Any = None,
    departure_time: Any = None,
    policy: SchedulePolicy | None = None,
) -> dict[str, Any]:
    """Lay tours across the trip, respecting slots and the post-landing rest.

    Greedy and stable: tours are placed in the order given (the caller sorts —
    recommended first), each on the earliest day it actually fits. A tour that
    fits nowhere lands in `excluded` with a reason the agent can quote.

    Returns {days: [...], excluded: [...], policy: {...}}.
    """
    p = policy or SchedulePolicy()
    try:
        d0 = datetime.strptime(str(start_date), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"error": True, "message": f"start_date must be yyyy-mm-dd, got {start_date!r}"}
    total_days = max(1, int(nights) + 1)

    days = [
        DayPlan(day_number=i + 1, date=(d0 + timedelta(days=i)).isoformat())
        for i in range(total_days)
    ]

    # --- Arrival day: flight, transfer, rest ---
    landed = _to_time(arrival_time)
    free_from: dict[int, time] = {}
    blocked_days: set[int] = set()
    if landed is not None:
        days[0].label = "Arrival"
        days[0].items.append(
            ScheduledItem(start=_fmt(landed), end="", title="Arrive in Dubai", kind="flight")
        )
        at_hotel = _to_time(hotel_checkin_time)
        hotel_carry = 0
        if at_hotel is None:
            at_hotel, hotel_carry = _add_minutes(landed, p.airport_to_hotel_min)
        days[0].items.append(
            ScheduledItem(
                start=_fmt(landed), end=_fmt(at_hotel),
                title=f"Airport transfer to {hotel_name}" if hotel_name else "Airport transfer",
                kind="transfer",
            )
        )
        free = earliest_free_time(arrival_time=landed, hotel_checkin_time=at_hotel, policy=p)
        # `hotel_carry` matters: a 23:30 landing puts the customer at the hotel
        # at 00:30 the NEXT day, so `free` comes back as a healthy-looking 03:30
        # that belongs to a different date. Without this the scheduler happily
        # booked a 07:00 tour on the arrival day.
        if hotel_carry or free is None or free > p.latest_activity_start:
            # Landed too late for anything today. Block the day outright —
            # setting a late "free from" time is not enough, because adding
            # transit to it wraps past midnight and the day looks open again.
            blocked_days.add(0)
            days[0].items.append(
                ScheduledItem(start=_fmt(at_hotel), end="", title="Check in and rest",
                              kind="free", detail="Late arrival — first full day is tomorrow")
            )
        else:
            free_from[0] = free
            days[0].items.append(
                ScheduledItem(
                    start=_fmt(at_hotel), end=_fmt(free), title="Check in and rest",
                    kind="free",
                    detail=f"{p.rest_after_landing_min // 60}h to settle in before any activity",
                )
            )

    # --- Departure day: keep it clear ---
    dep = _to_time(departure_time)
    if dep is not None and total_days > 1:
        days[-1].label = "Departure"
        days[-1].items.append(
            ScheduledItem(start=_fmt(dep), end="", title="Departure flight", kind="flight")
        )

    # --- Place the tours ---
    excluded: list[ExcludedItem] = []
    cursor: dict[int, time] = {}  # day index -> when the day is free again

    # Longest tours first. A greedy pass in caller order strands long tours: a
    # 30-minute fountain show taken at 17:45 pushes the day's cursor past 18:00,
    # and a 6-hour safari that would have fitted from 09:00 then fits nowhere.
    # Sorting by duration keeps the caller's order as the tie-break, so
    # recommended-first still decides between tours of similar length.
    ordered = [t for t in (tours or []) if isinstance(t, dict)]
    ordered.sort(key=lambda t: -_activity_minutes(t, p))

    # Tours per day so far — used to SPREAD the trip rather than front-load it.
    # Greedy first-fit put three tours on arrival day and left four days empty,
    # which reads as a badly planned holiday even though every placement was
    # individually legal.
    def _booked_min(i: int) -> int:
        """Activity minutes already committed on a day."""
        total = 0
        for it in days[i].items:
            if it.kind != "tour":
                continue
            s, e = _to_time(it.start), _to_time(it.end)
            if s and e:
                total += max(0, (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute))
        return total

    for tour in ordered:
        placed = False
        reasons: list[str] = []
        # Fill each day toward the activity budget before moving on: a day with
        # a 1-hour kayak still has ~6 hours going spare and should take another
        # tour. Days already at/over budget are tried last, so we spread only
        # once the earlier days are genuinely full — the customer came to DO
        # things, not to wait for tomorrow.
        order = sorted(
            range(len(days)),
            key=lambda i: (_booked_min(i) >= p.target_activity_min_per_day, i),
        )
        for idx in order:
            day = days[idx]
            # Don't stack activities onto the departure day.
            if dep is not None and idx == len(days) - 1 and total_days > 1:
                continue
            if idx in blocked_days:
                continue
            # A day is full once it hits the activity count cap, even if minutes
            # remain — six short tickets in one day is not a holiday.
            if sum(1 for it in day.items if it.kind == "tour") >= p.max_activities_per_day:
                continue
            not_before = cursor.get(idx) or free_from.get(idx) or p.day_start
            item, why = _place_on_day(tour, not_before=not_before, policy=p)
            if item is None:
                if why:
                    reasons.append(f"day {day.day_number}: {why}")
                continue
            day.items.append(item)
            end_t = _to_time(item.end) or not_before
            cursor[idx] = end_t
            placed = True
            break
        if not placed:
            title = str(tour.get("name") or tour.get("title") or "Tour").strip()
            excluded.append(
                ExcludedItem(
                    title=title,
                    reason=reasons[0].split(": ", 1)[-1] if reasons else "no day had room",
                    detail="; ".join(reasons[:3]),
                )
            )

    # Sort by start time, but keep the arrival-day narrative in order: a
    # post-midnight hotel check-in (00:30 after a 23:30 landing) would otherwise
    # sort to the TOP of the day, above the flight that caused it.
    for idx, d in enumerate(days):
        late_arrival = idx == 0 and landed is not None and landed.hour >= 20

        def _key(i: ScheduledItem, _late: bool = late_arrival) -> tuple[int, str]:
            start = i.start or "99:99"
            # Times before 06:00 on a late-arrival day belong after the evening.
            if _late and start < "06:00":
                return (1, start)
            return (0, start)

        d.items.sort(key=_key)

    # Days with no activity at all. The scheduler cannot invent tours, so an
    # under-filled trip is a signal for the CALLER to search for more — the
    # agent previously presented "Free day" three times over and called it a
    # complete itinerary.
    empty: list[int] = []
    for idx, d in enumerate(days):
        if idx in blocked_days:
            continue
        if dep is not None and idx == len(days) - 1 and total_days > 1:
            continue
        if not any(i.kind == "tour" for i in d.items):
            empty.append(d.day_number)

    return {
        "days": [d.as_dict() for d in days],
        "excluded": [e.as_dict() for e in excluded],
        "empty_days": empty,
        "planning_note": (
            f"Days {', '.join(map(str, empty))} have no activity. Search for more "
            f"tours and call this again with a longer list — do NOT present a "
            f"'free day' as a finished plan unless the customer asked for one."
            if empty
            else "Every day has at least one activity."
        ),
        "policy": {
            "rest_after_landing_min": p.rest_after_landing_min,
            "transit_to_activity_min": p.transit_to_activity_min,
            "latest_activity_start": _fmt(p.latest_activity_start),
        },
    }
