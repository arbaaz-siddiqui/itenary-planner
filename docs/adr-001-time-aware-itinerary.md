# ADR-001: Time-aware itinerary scheduling

**Status:** Proposed
**Date:** 2026-08-19
**Deciders:** Product owner (arbaaz-siddiqui)

## Context

The agent can price a trip but cannot schedule one. Three gaps were verified
against the live API on 2026-08-19, not inferred:

1. **Timeslots never reach the model.** `call_tour_timeslots` is exported from
   `booking_api/__init__.py` and called by nothing. Burj Khalifa
   (tourId 30614) really does return 33 half-hourly slots, 07:00–23:00, each
   with a live seat count. The agent has no way to see them.

2. **No time feasibility exists anywhere.** `check_floor_tool` validates
   *budget*. Nothing validates *time*: no rest gap after a flight, no travel
   time, no conflict detection, no check that a chosen slot is still open.
   `build_trip_schedule_tool` accepts `start`/`end` as free-text strings and
   validates nothing — every time in a rendered schedule today is invented by
   the model.

3. **`is_recommended` is parsed but never used.** `parsers.py:550` reads the
   supplier's `isRecommanded` flag into `TourOption.is_recommended`, and
   `search_tours` never sorts on it. Results come back in supplier order.

A blocking plumbing detail: `TourOption` carries `supplier_name` but **not
`supplier_id`, and no option id at all**. The timeslot endpoint requires both
(`tour_option_id` + `supplier_id`), and they only appear in the *rates*
response, keyed by `tourID`. So no slot lookup is possible until `search_tours`
threads those two ids through.

### Data quality constraints discovered

These shape the design and cannot be wished away:

- **25% of tours return a placeholder `00:00` slot.** Rendering it naively tells
  a customer their tour starts at midnight. Slots must be filtered, not trusted.
- **41% of tours have no slots at all** — they are not slot-based and must
  schedule by duration alone.
- Slot support is **supplier-dependent**: klook returns slots (half real, half
  placeholder), Rayna almost never (20 of 23 sampled returned none), Emaar yes.
- `tourrating` and `reviewsCount` are **0 for all 332 Dubai tours**, so ranking
  cannot use rating. `duration` is present on only 191/332.
- `settingtypes` containing `{settingTypeId: 8, 'Has Optionwise Timeslot'}` is
  the reliable signal that a tour is slot-based.

### Requirements

- A tour must not be placed on a day where its last slot falls before the
  customer is free (the Burj-Khalifa-on-arrival-day case).
- **3 hours' rest after landing** before any activity. No inter-activity gap
  (explicit product decision — activities may run back-to-back after that).
- Day-by-day tables with concrete times, and the reason anything was excluded.
- First 3 tours shown are the recommended ones; more on request.

## Decision

Add a **deterministic scheduling layer**. Python computes the schedule; the
model only narrates it. This mirrors the existing, working "MATH — never do it
yourself" rule: the model has already proven it invents times when asked to
reason about them.

Three parts:

1. **Thread the ids** — add `supplier_id` and `option_id` to `TourOption`, and
   an `is_slot_based` flag derived from `settingtypes` id 8.
2. **`get_tour_timeslots` tool** — fetch slots for a tour, drop `00:00`
   placeholders and zero-availability rows, return real times.
3. **`plan_itinerary_tool`** — given arrival time, hotel and chosen tours,
   return a validated day-by-day plan plus an explicit list of what could not
   be placed and why.

## Options Considered

### Option A: Deterministic tool decides, model narrates (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Medium — one pure scheduling module, well unit-testable |
| Cost | One extra API call per slot-based tour |
| Scalability | Fine — slot lookups parallelise, results cacheable |
| Team familiarity | High — same shape as `check_floor_tool` |

**Pros:** times cannot be hallucinated; feasibility is testable without an LLM;
exclusion reasons are exact and quotable; deterministic output is cacheable.
**Cons:** less flexible for genuinely unusual requests; scheduling policy is in
code, so changing rest rules is a deploy not a prompt edit.

### Option B: Model drafts, validator rejects, model retries

| Dimension | Assessment |
|---|---|
| Complexity | Medium — validator plus a retry loop |
| Cost | High — 2–4 extra LLM round-trips per itinerary |
| Scalability | Poor — voice turns already exceed 20s |
| Team familiarity | Medium |

**Pros:** handles odd requests gracefully; policy adjustable via prompt.
**Cons:** each retry is a full round-trip on a surface where latency is already
the top complaint; can fail to converge; the model still needs slot data, so
this does not avoid the plumbing work — it only adds a loop on top.

### Option C: Prompt rules only

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | Zero build |
| Scalability | N/A |
| Team familiarity | High |

**Pros:** ships immediately.
**Cons:** does not work. The model has no slot data, so it would keep inventing
times — the exact failure being fixed. Rejected as not addressing the gap.

## Trade-off Analysis

The decisive factor is **falsifiability**. A rest-gap rule in a prompt cannot be
tested; the same rule in Python gets a unit test that fails when broken. Given
that every schedule time in the product today is fabricated, moving this into
code converts an unverifiable behaviour into a verified one.

The cost is real but bounded: scheduling policy changes need a deploy. That is
acceptable because these rules (3h rest, slot windows) are business invariants,
not per-conversation preferences.

Option B was rejected primarily on latency. Voice turns already run 20s+ with
live searches; adding LLM round-trips to a *scheduling* step would make the
itinerary flow the slowest path in the product.

## Consequences

**Easier**
- Times become trustworthy; "last slot is 18:00, so this moves to day 2" is a
  computed fact with a stated reason.
- Rest/transit policy lives in one place with tests.
- Slot availability finally reaches the customer at all.

**Harder**
- One extra API call per slot-based tour (mitigated by the existing result
  cache and by only querying tours the customer actually picked).
- `TourOption` gains fields, so the PDF and card renderers need checking.

**To revisit**
- Slots are fetched for a *specific date*; a multi-day plan needs one lookup per
  tour per candidate day. Start with the chosen day only.
- Travel time is currently a flat allowance. Real point-to-point duration would
  need a distance/matrix source we do not have.
- `/api/tours/options` returns **404** for every tour tried, so `get_tour_options`
  is dead. Confirm with the client whether the path is wrong or the endpoint is
  retired.

## Action Items

1. [ ] Add `supplier_id`, `option_id`, `is_slot_based` to `TourOption`; thread
       them through `parse_tour_response` and `search_tours`.
2. [ ] Build `scheduling.py` — pure functions, no I/O: slot filtering,
       earliest-free-time, day assignment, exclusion reasons.
3. [ ] Add `get_tour_timeslots` tool (filters `00:00` placeholders).
4. [ ] Add `plan_itinerary_tool` returning day tables + `excluded[]` with reasons.
5. [ ] Sort `search_tours` by `is_recommended` first, then price.
6. [ ] Unit tests: the Burj-on-arrival-day case, placeholder slots, no-slot
       tours, back-to-back activities.
7. [ ] Prompt: teach the agent to call the planner and relay `excluded` reasons
       verbatim.
8. [ ] Ask the client: why does `/api/tours/options` 404, and does `00:00` mean
       "no fixed time" or is it a data bug?
