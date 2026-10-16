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
from dataclasses import replace, dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

# =============================================================================
# Policy
# =============================================================================
# Rest after landing before the first activity. Product decision (2026-10-16):
# 1-2 hours, then the day continues; NO inter-activity gap beyond transit.
DEFAULT_REST_AFTER_LANDING_MIN = 120
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
    # hours used to be the target; the customer came for the trip and wants
    # the whole day used (product decision 2026-10-16), so it is 9 hours of
    # activity around breakfast, lunch and dinner.
    target_activity_min_per_day: int = 540
    # Even short activities need travel, queuing and a meal between them. Four
    # in a day is busy; more reads as a conveyor belt. Guarding only on minutes
    # let six 30-minute tickets stack onto one day.
    max_activities_per_day: int = 4
    # Meals are part of the day plan: breakfast at the hotel, lunch near the
    # midday activity, dinner after the evening one (or inside a tour that
    # includes it). Off for callers that only want tour placement.
    plan_meals: bool = True
    meal_min: int = 60
    breakfast_at: time = time(8, 0)
    lunch_earliest: time = time(12, 30)
    lunch_latest_start: time = time(15, 0)
    dinner_earliest: time = time(19, 30)
    dinner_latest_start: time = time(22, 0)
    # At the airport this long before an international flight.
    airport_before_departure_min: int = 180
    checkout_time: time = time(12, 0)


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
        # Search results carry the start as "time" (the supplier's raw rows use
        # "timeSlot"); reading only the latter dropped every published slot, so
        # Burj Khalifa was scheduled by duration at 20:00 and then excluded.
        t = parse_slot_time(s.get("timeSlot") or s.get("time_slot") or s.get("time"))
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

    # For a meal: the activity it follows, so a venue near it can be chosen.
    near: str = ""
    # For a meal inside a tour (dinner cruise, safari BBQ): included, no venue.
    included: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = {"start": self.start, "end": self.end, "title": self.title,
             "kind": self.kind, "detail": self.detail}
        if self.slot_id:
            d["slot_id"] = self.slot_id
        if self.near:
            d["near"] = self.near
        if self.included:
            d["included"] = True
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


# When an activity can start at the earliest, by what it is. The supplier gives
# no start time for most tours (Tourdetails `startTime` is empty), so a dinner
# cruise was scheduled at 09:45. Matched on the tour's name and category; the
# first matching rule wins, and a tour with published slots uses those instead.
_TIME_OF_DAY_FLOORS: tuple[tuple[re.Pattern[str], time], ...] = (
    (re.compile(r"\b(dinner|night|evening|nocturnal|after dark)\b", re.I), time(19, 30)),
    (re.compile(r"global village", re.I), time(16, 0)),
    (re.compile(r"\b(sunset|desert safari|dune bash\w*|safari)\b", re.I), time(15, 0)),
)


def time_of_day_floor(tour: dict[str, Any]) -> time | None:
    """The earliest sensible start for this kind of activity, or None."""
    text = " ".join(str(tour.get(k) or "") for k in ("name", "title", "category", "option_name"))
    for pattern, floor in _TIME_OF_DAY_FLOORS:
        if pattern.search(text):
            return floor
    return None


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

    # No published slots: schedule by duration, but not before the hour this
    # kind of activity happens at (a dinner cruise in the evening).
    floor = time_of_day_floor(tour)
    if floor and ready < floor:
        ready = floor
    # A daytime activity finishes by the evening, so the day ends with dinner:
    # Museum of the Future had been booked 21:00-23:00 with no dinner at all.
    if floor is None and policy.plan_meals:
        policy = replace(policy, latest_activity_end=min(policy.latest_activity_end,
                                                         DAYTIME_LATEST_END, key=_minutes))
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


# A tour that serves the meal itself: the dinner cruise, the safari BBQ.
_MEAL_IN_TOUR = ("dinner", "bbq", "barbecue", "buffet")
# Latest finish for a daytime activity when meals are planned.
DAYTIME_LATEST_END = time(20, 0)
# An activity starting from 18:30 is the day's evening activity.
EVENING_FROM_MIN = 18 * 60 + 30


def serves_dinner(tour: dict[str, Any]) -> bool:
    """A tour that is the evening meal: by name, or as its inclusions say."""
    name = str(tour.get("name") or tour.get("title") or "").lower()
    return bool(tour.get("includes_dinner")) or any(w in name for w in _MEAL_IN_TOUR)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _at(minutes: int) -> time:
    minutes = max(0, min(minutes, 24 * 60 - 1))
    return time(minutes // 60, minutes % 60)


def _gap_for(day: DayPlan, start: time, latest: time, length: int) -> time | None:
    """The first time from `start` (no later than `latest`) with `length`
    free minutes before the next timed item."""
    timed = []
    for i in day.items:
        s = _to_time(i.start)
        if s is None:
            continue
        e = _to_time(i.end) or s
        timed.append((_minutes(s), max(_minutes(e), _minutes(s))))
    timed.sort()
    cand = _minutes(start)
    for s, e in timed:
        if e <= cand:
            continue
        if s - cand >= length:
            break
        cand = max(cand, e)
    if cand > _minutes(latest) or cand + length >= 24 * 60:
        return None
    return _at(cand)


def _add_meals(days: list[DayPlan], *, policy: SchedulePolicy, hotel_name: str,
               lunch_on: set[int], blocked_days: set[int], has_departure: bool,
               landed: time | None, serves_dinner: set[str] | None = None) -> None:
    """Breakfast, lunch and dinner on every day the customer is in Dubai.

    Breakfast is at the hotel; lunch and dinner name the activity they follow
    (`near`) so the caller can pick a venue close to it, and a tour that
    serves the meal (a dinner cruise) is that meal.
    """
    p = policy
    last = len(days) - 1
    for idx, day in enumerate(days):
        tours = [i for i in day.items if i.kind == "tour"]
        # Breakfast at the hotel, from the first morning there.
        in_dubai = idx > 0 or (landed is not None and _minutes(landed) < _minutes(p.breakfast_at))
        if hotel_name and in_dubai:
            starts = [_to_time(i.start) for i in day.items if _to_time(i.start) and i.kind != "hotel"]
            first_out = min(starts, key=_minutes) if starts else None
            if first_out is None or _minutes(first_out) >= _minutes(p.breakfast_at) + p.meal_min:
                day.items.append(ScheduledItem(
                    start=_fmt(p.breakfast_at), end=_fmt(_at(_minutes(p.breakfast_at) + p.meal_min)),
                    title=f"Breakfast at {hotel_name}", kind="meal"))
        if idx in blocked_days:
            continue
        # Lunch in a free hour around midday, if the day did not get one.
        if idx not in lunch_on and (tours or idx == 0):
            # A full hour, or a quick 45 minutes between two activities.
            length = p.meal_min
            at = _gap_for(day, p.lunch_earliest, p.lunch_latest_start, length)
            if at is None:
                length = 45
                at = _gap_for(day, p.lunch_earliest, p.lunch_latest_start, length)
            if at is not None:
                before = [i for i in tours if _to_time(i.end) and _minutes(_to_time(i.end)) <= _minutes(at)]
                day.items.append(ScheduledItem(start=_fmt(at), end=_fmt(_at(_minutes(at) + length)),
                                               title="Lunch", kind="meal",
                                               near=before[-1].title if before else ""))
        if has_departure and idx == last:
            continue
        # Dinner: inside a tour that serves it, else after the day's last activity.
        serving = next((i for i in tours if i.title in (serves_dinner or set())
                        or any(w in i.title.lower() for w in _MEAL_IN_TOUR)), None)
        if serving is not None:
            s_start, s_end = _to_time(serving.start), _to_time(serving.end)
            at_t = s_start
            if s_start and _minutes(s_start) < _minutes(p.dinner_earliest):
                at_t = p.dinner_earliest
                if s_end and _minutes(s_end) < _minutes(at_t):
                    at_t = _at(max(_minutes(s_start), _minutes(s_end) - p.meal_min))
            day.items.append(ScheduledItem(start=_fmt(at_t) if at_t else serving.start, end="",
                                           title=f"Dinner (included in {serving.title})",
                                           kind="meal", included=True))
            continue
        ends = [_to_time(i.end) for i in day.items if _to_time(i.end) and i.kind != "meal"]
        after = max([p.dinner_earliest, *[e for e in ends if e]], key=_minutes)
        at = _gap_for(day, after, p.dinner_latest_start, p.meal_min)
        if at is not None:
            done = [i for i in tours if _to_time(i.end) and _minutes(_to_time(i.end)) <= _minutes(at)]
            day.items.append(ScheduledItem(start=_fmt(at), end=_fmt(_at(_minutes(at) + p.meal_min)),
                                           title="Dinner", kind="meal",
                                           near=done[-1].title if done else ""))
            continue
        # An evening visit that runs through dinner time (Global Village until
        # 23:00): dinner is eaten there, so it is placed during the visit.
        evening = next((i for i in tours if _to_time(i.start) and _to_time(i.end)
                        and _minutes(_to_time(i.start)) <= _minutes(p.dinner_latest_start)
                        and _minutes(_to_time(i.end)) >= _minutes(p.dinner_earliest) + p.meal_min), None)
        if evening is not None:
            start_e = max(_minutes(_to_time(evening.start)), _minutes(p.dinner_earliest) + 30)
            day.items.append(ScheduledItem(start=_fmt(_at(start_e)), end=_fmt(_at(start_e + p.meal_min)),
                                           title="Dinner", kind="meal", near=evening.title,
                                           detail=f"During the visit to {evening.title}"))


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
        reach, reach_carry = _add_minutes(landed, p.airport_to_hotel_min)
        # A check-in time earlier than landing plus the drive is the hotel's
        # standard check-in, not when this customer gets there: "Check in 14:00"
        # printed under an 18:35 landing. They arrive when the transfer does.
        # A later one is the hotel's standard check-in too: the customer still
        # reaches the hotel when the transfer does and rests from then. Read as
        # the arrival, a 14:00 check-in after a 10:20 landing pushed the rest to
        # 16:00 and the 15:00 desert safari fell out of the plan.
        standard_checkin = at_hotel if (at_hotel is not None and not reach_carry and at_hotel > reach) else None
        if at_hotel is None or reach_carry or at_hotel != reach:
            at_hotel, hotel_carry = reach, reach_carry
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
                    detail=f"{p.rest_after_landing_min // 60}h to settle in before any activity"
                    + (f"; standard check-in from {_fmt(standard_checkin)}, early check-in on request"
                       if standard_checkin else ""),
                )
            )

    elif hotel_name:
        # No flight times: still say where the trip starts. A first day with
        # nothing on it printed as a bare "Day 1" heading in the PDF. Untimed,
        # because no arrival time was given to time it by.
        days[0].label = "Arrival"
        days[0].items.append(
            ScheduledItem(start="", end="", title=f"Check in at {hotel_name}", kind="hotel")
        )

    # --- Departure day: check out, airport drop-off, flight ---
    dep = _to_time(departure_time)
    # When the departure day still has room for an activity: until the
    # customer must head back to the hotel for the airport drop-off.
    dep_free_until: time | None = None
    if dep is not None and total_days > 1:
        days[-1].label = "Departure"
        at_airport = _minutes(dep) - p.airport_before_departure_min
        leave_hotel = at_airport - p.airport_to_hotel_min
        if leave_hotel >= 0:
            lt = time(leave_hotel // 60, leave_hotel % 60)
            at = time(at_airport // 60, at_airport % 60)
            checkout = min(lt, p.checkout_time, key=_minutes)
            if hotel_name:
                days[-1].items.append(
                    ScheduledItem(start=_fmt(checkout), end="", title=f"Check out of {hotel_name}",
                                  kind="hotel")
                )
            days[-1].items.append(
                ScheduledItem(start=_fmt(lt), end=_fmt(at),
                              title=f"Airport drop-off from {hotel_name}" if hotel_name else "Airport drop-off",
                              kind="transfer",
                              detail=f"At the airport {p.airport_before_departure_min // 60}h before the flight")
            )
            # Back at the hotel 30 minutes before the drop-off leaves.
            # And by check-out: Miracle Garden had run 09:45-13:45 across a
            # 12:00 check-out.
            back = min(leave_hotel - 30, _minutes(p.checkout_time))
            if back >= _minutes(p.day_start) + 90:
                dep_free_until = time(back // 60, back % 60)
        days[-1].items.append(
            ScheduledItem(start=_fmt(dep), end="", title="Departure flight", kind="flight")
        )
    elif hotel_name and total_days > 1:
        days[-1].label = "Departure"
        days[-1].items.append(
            ScheduledItem(start="", end="", title=f"Check out of {hotel_name}", kind="hotel")
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
    # Afternoon and evening activities (a safari, a dinner cruise) are placed
    # first and reserve their part of the day; daytime tours then fill the
    # morning and afternoon before it. Placed last, the safari found every day
    # already full and was dropped from a 4-night plan.
    # The customer's own choices are placed before tours the plan added to
    # fill the days: Burj Khalifa, asked for, had lost its slot to fillers.
    ordered.sort(key=lambda t: (bool(t.get("auto_filled")), time_of_day_floor(t) is None,
                                -_activity_minutes(t, p)))
    # A tour priced for a particular date goes on THAT day, placed before the
    # free-floating ones so they cannot take its slot. The Desert Safari was
    # priced for 3 October and scheduled on 5 October; the customer would have
    # paid one date's rate for another date's booking.
    index_of = {d.date: i for i, d in enumerate(days)}

    def _pinned(t: dict[str, Any]) -> int | None:
        return index_of.get(str(t.get("date") or "")[:10])

    ordered.sort(key=lambda t: _pinned(t) is None)

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

    lunch_on: set[int] = set()
    # Day index -> when that day's reserved afternoon/evening activity starts.
    evening_from: dict[int, time] = {}
    dinner_tours = {
        str(t.get("name") or t.get("title") or "").strip() for t in ordered if serves_dinner(t)
    }

    def _last_activity(day: DayPlan) -> str:
        tours_so_far = [i for i in day.items if i.kind == "tour" and i.end]
        return tours_so_far[-1].title if tours_so_far else ""

    for tour in ordered:
        placed = False
        reasons: list[str] = []
        # Fill each day toward the activity budget before moving on: a day with
        # a 1-hour kayak still has ~6 hours going spare and should take another
        # tour. Days already at/over budget are tried last, so we spread only
        # once the earlier days are genuinely full — the customer came to DO
        # things, not to wait for tomorrow.
        # The arrival day is a half day: half the activity budget.
        def _budget(i: int) -> int:
            return p.target_activity_min_per_day // 2 if i == 0 and landed is not None else p.target_activity_min_per_day

        order = sorted(range(len(days)), key=lambda i: (_booked_min(i) >= _budget(i), i))
        pin = _pinned(tour)
        if pin is not None:
            # Its own date first; when that day cannot take it, another day
            # rather than nowhere. Swapping AYA Universe for Dubai Frame on a
            # day that was already full dropped Dubai Frame from the plan, and
            # the customer's own pick vanished. The plan prices every tour on
            # the day it is scheduled, so a moved tour is priced for that day.
            order = [pin] + [i for i in order if i != pin]
        def _attempt(allow_skip_lunch: bool) -> bool:
            for idx in order:
                day = days[idx]
                day_policy = p
                # The departure day takes an activity only when it finishes
                # before the customer must be back for the airport drop-off.
                if dep is not None and idx == len(days) - 1 and total_days > 1:
                    if dep_free_until is None:
                        continue
                    day_policy = replace(p, latest_activity_end=min(p.latest_activity_end, dep_free_until,
                                                                    key=_minutes))
                if idx in blocked_days:
                    continue
                # One dinner per evening: the safari BBQ and a dinner cruise
                # had both landed on the same night.
                if serves_dinner(tour) and any(i.title in dinner_tours for i in day.items):
                    continue
                # A day is full once it hits the activity count cap, even if
                # minutes remain: six short tickets in one day is not a holiday.
                cap = (2 if idx == 0 and landed is not None
                       # The departure day: one activity, then lunch and packing.
                       else 1 if dep is not None and idx == len(days) - 1 and total_days > 1
                       else p.max_activities_per_day)
                if sum(1 for it in day.items if it.kind == "tour") >= cap:
                    continue
                if idx == 0 and landed is not None and _booked_min(idx) >= _budget(idx):
                    continue
                not_before = cursor.get(idx) or free_from.get(idx) or p.day_start
                floor_t = time_of_day_floor(tour)
                if floor_t is not None and idx in evening_from:
                    continue  # one reserved afternoon/evening activity per day
                if floor_t is None and idx in evening_from:
                    cap_t = _at(_minutes(evening_from[idx]) - p.transit_to_activity_min)
                    day_policy = replace(day_policy, latest_activity_end=min(
                        day_policy.latest_activity_end, cap_t, key=_minutes))
                # Lunch where the day reaches midday between activities, but
                # only when the next activity still fits after it.
                lunch: ScheduledItem | None = None
                item, why = None, ""
                at_midday = (p.plan_meals and idx not in lunch_on and floor_t is None
                             and p.lunch_earliest <= not_before <= p.lunch_latest_start)
                if at_midday:
                    end_l, carry_l = _add_minutes(not_before, p.meal_min)
                    if not carry_l:
                        item, why = _place_on_day(tour, not_before=end_l, policy=day_policy)
                        if item is not None:
                            lunch = ScheduledItem(start=_fmt(not_before), end=_fmt(end_l), title="Lunch",
                                                  kind="meal", near=_last_activity(day))
                if item is None:
                    if at_midday and not allow_skip_lunch:
                        # It fits only by skipping lunch: try the other days first.
                        reasons.append(f"day {day.day_number}: would leave no time for lunch")
                        continue
                    item, why = _place_on_day(tour, not_before=not_before, policy=day_policy)
                if item is None:
                    if why:
                        reasons.append(f"day {day.day_number}: {why}")
                    continue
                # One evening activity per day: a slot from 18:30 is the
                # evening (the Dolphinarium show at 19:15 and the fountain show
                # at 22:15 had both landed on one night, over dinner).
                starts_evening = _to_time(item.start) and _minutes(_to_time(item.start)) >= EVENING_FROM_MIN
                if starts_evening and idx in evening_from:
                    reasons.append(f"day {day.day_number}: that evening is already planned")
                    continue
                if lunch is not None:
                    day.items.append(lunch)
                    lunch_on.add(idx)
                day.items.append(item)
                if (floor_t is not None or starts_evening) and _to_time(item.start):
                    # Reserves the evening; daytime tours still fill the day.
                    evening_from[idx] = _to_time(item.start)
                else:
                    cursor[idx] = _to_time(item.end) or not_before
                return True
            return False

        placed = _attempt(False) or _attempt(True)
        if not placed:
            title = str(tour.get("name") or tour.get("title") or "Tour").strip()
            if pin is not None and not reasons:
                reasons = [f"day {days[pin].day_number}: {days[pin].date}, the date it was "
                           "priced for, is the arrival-rest or departure day or already full"]
            excluded.append(
                ExcludedItem(
                    title=title,
                    reason=reasons[0].split(": ", 1)[-1] if reasons else "no day had room",
                    detail="; ".join(reasons[:3]),
                )
            )

    if p.plan_meals:
        # Tours that serve dinner although their name does not say so (the
        # desert safari's BBQ), as the caller read from their inclusions.
        _add_meals(days, policy=p, hotel_name=hotel_name, lunch_on=lunch_on,
                   blocked_days=blocked_days, has_departure=dep is not None and total_days > 1,
                   landed=landed, serves_dinner=dinner_tours)

    # Sort by start time, but keep the arrival-day narrative in order: a
    # post-midnight hotel check-in (00:30 after a 23:30 landing) would otherwise
    # sort to the TOP of the day, above the flight that caused it.
    for idx, d in enumerate(days):
        late_arrival = idx == 0 and landed is not None and landed.hour >= 20

        def _key(i: ScheduledItem, _late: bool = late_arrival) -> tuple[int, str]:
            # An untimed hotel check-in opens the day it is on.
            if i.kind == "hotel" and not i.start and i.title.startswith("Check in"):
                return (-1, "")
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
        # The last day of a multi-day trip is the check-out and travel day,
        # with or without a departure time. Counting it "empty" made a
        # finished 2-night plan look incomplete, and the model threw the plan
        # away and replied with option tables.
        if idx == len(days) - 1 and total_days > 1:
            continue
        if not any(i.kind == "tour" for i in d.items):
            empty.append(d.day_number)

    return {
        "days": [d.as_dict() for d in days],
        "excluded": [e.as_dict() for e in excluded],
        "empty_days": empty,
        "planning_note": (
            f"Days {', '.join(map(str, empty))} have no activity. Search for more "
            f"tours and call this again with a longer list, unless the customer "
            f"chose these tours (or how many): then present this plan as it is "
            f"and offer to add a tour on those days. Never drop a built plan for "
            f"a list of options, and never call an empty day a planned 'free day'."
            if empty
            else "Every day has at least one activity."
        ),
        "policy": {
            "rest_after_landing_min": p.rest_after_landing_min,
            "transit_to_activity_min": p.transit_to_activity_min,
            "latest_activity_start": _fmt(p.latest_activity_start),
        },
    }
