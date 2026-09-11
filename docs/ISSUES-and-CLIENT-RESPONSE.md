# Issues found, root causes, and the response to the client review

Rewritten 10 September 2026. Every figure here was measured against the live
supplier API or the running code, not inferred.

---

## Part 1 — Response to the client team's review

### API failure / staging URL — fixed

Two tour endpoints (`/api/tours/options`, `/api/tours/optionRate`) are served
**only** on `www.gujjutours.com`. The documented `stagingb2c` host returns an
nginx HTML 404 on every `/api` path, so a request routed there got HTML where
JSON was expected.

Proven both ways:

```
stagingb2c.gujjutours.com  -> HTTP 404, HTML body, 0 variants
www.gujjutours.com         -> HTTP 200, 3 variants
```

It did not drop the connection. Response bodies are parsed defensively in three
places, so the HTML became a typed error rather than an unhandled exception —
without that, an error page would crash the turn in front of the customer.

The real damage was the customer-facing reply: the failure carried no recovery
instruction, so the agent apologised for a "technical glitch" and asked which
hotel the customer was staying at (irrelevant to tour variants) while
correctly-priced tours were already on screen. Both fixed, and the correct host
is now a code default rather than only an env var.

The `Unexpected token '<'` JSON parse error was the same single cause — the
debug tab reporting that an HTML body was not JSON. It disappears with the host
fix.

### Call volume — confirmed, and structural

Measured on a live 10-tour search: **22 calls** warm, 42 worst case.

```
 10  /TourSearch/Timeslot
  5  /tours/optionRate
  3  /tours/options
  2  /Currency/ROE/INR
  1  /TourSearch/toursearchlist
  1  /TourSearch/toursearchlistrate
```

The supplier needs four calls in strict order, because each returns the ID the
next one requires:

```
toursearchlist      -> tour_id
toursearchlistrate  -> base price for that tour_id
/tours/options      -> option_id + supplierId   (needs tour_id)
/tours/optionRate   -> sharing/private price    (needs option_id)
```

No combined endpoint exists, so `N` results means `N` sequences: 42 calls at ten
tours, 82 on a named search, 162 after one "show me more". They run 24-wide with
a hard deadline, which is what keeps a desert safari search at ~8 seconds
instead of ~50. Cutting the fan-out loses the transfer prices and falls back to
"price on request".

Flights are the same shape. The search is keyed on an exact airport pair, and
Dubai has both DXB and DWC while several Indian origin cities map to more than
one code — so "cheapest Delhi to Dubai" means checking each valid pair and
merging. Querying one code would mean confidently naming a fare that is not the
cheapest.

### Duplicate calls — confirmed, ours to fix

`call_tour_options` fires with a byte-identical payload from both
`search_tours.py:91` and `get_tour_options.py:93`. The raw endpoint has no cache
— only the tools above it do, under different keys that can never share an
entry — and our own prompt tells the agent to chain the two, so the normal
search-then-drill-down flow refetches every time.

Also found: the transfer-tier probe is capped at 2 attempts in `search_tours`
but was left uncapped at 4 in `get_tour_options`. The same defensive fix had
been applied in one file and missed in the other — worth ~30 extra calls on a
15-variant tour.

### Payload size and instruction bloat — confirmed

Live 10-tour search: **21,206 bytes (~5,300 tokens)**, with `agent_instructions`
alone at **501 words / 2,983 bytes**, repeated on every tool result.

Prompt caching is the plan, but measured rather than assumed — it is already
active and the hit rate is poor:

```
call 1: 12,923 prompt tokens ->     5 cached
call 2: 23,269 prompt tokens ->     0 cached
```

Caching only helps a stable prefix. The system prompt and tool schemas cache
well; tool *results* change every turn and cannot. So caching removes the
repeated instructions and not the growing result payload — both are needed.

On sending less data generally: in a normal API the client filters because it
knows the question before it calls. Here the question is arbitrary. "Hotels with
a swimming pool" needs the amenity text to reach the model; "which allows free
cancellation" needs the policy field. Each looks prunable until someone asks
that exact question, and the failure mode is not a missing answer but a
confident wrong one, because with the data absent the model answers from memory.

So: cut redundancy now (instruction text per result, image URLs on voice and
WhatsApp where nothing renders them); leave question-driven field selection for
the production phase, with tests.

### Empty JSON keys — client was right, and I was wrong first time

I initially reported this as not reproducible. That was wrong. The
`toursearchlistrate` response really does use **blank strings as JSON keys** —
visible in the debug tab as `"":[...]`.

It is not corrupting our data (we read `result` as a list), but the observation
was correct and it originates on the supplier side.

### ROE re-fetched — confirmed, smaller than described

Exactly **2 fetches per cold turn**, not one per tour, because `fx.py` has two
caches that do not share storage. Both coalesce concurrent callers correctly.

The larger version was already fixed: a 15-tour parallel search used to fetch
ROE 15 times, and parsing a 270-tour list once took 43 seconds.

A third path, `get_exchange_rate.py:56`, bypasses both caches on a 180s tool TTL
that is shorter than fx's own 300s, so it can force a fresh call while the fx
caches are still warm.

### Infants — confirmed, the most serious item

The client's framing — the AI understood the user but the value was lost between
the tools — was exactly the mechanism.

`resolve_party()` has **no infants parameter**. It derives infants from
`child_ages` by counting anyone under 2:

```
"2 adults, 3 infants"  (no ages) -> infants: 0, children: 3
                                    the 3 is lost; all priced as children
same party, ages [1,1,1]         -> infants: 3   correct
```

Data loss in a function signature, not a prompt problem — the model passes the
value correctly and the function cannot receive it.

**1 adult + 19 infants** is a separate bug: room allocation counts adults only,
so it returns **one room for nineteen infants**. For a safari that would confirm
a booking the vehicle physically cannot carry, failing at the supplier rather
than in our reply.

### Dubai / Abu Dhabi ordering — confirmed, different cause than assumed

Worse than reported: "city tour" returns three Abu Dhabi results then
**Putrajaya, Malaysia**.

But it is not keyword matching beating location — we *do* rank by city. The
cause is upstream: we request 5 rows and Dubai does not appear in the supplier's
own ordering until around 20.

```
size=5  ->  0 in Dubai
size=20 ->  2 in Dubai
size=40 -> 12 in Dubai
```

Twelve Dubai desert safaris are sitting at size 40. At size 5 the city ranking
has nothing to rank. Two defects in two lines: a default too small
(`lookup_entity.py:73`) and a cap below where the requested city appears
(`:144`).

### Timeslot calls — confirmed

Live 10-tour page: **10 Timeslot calls, only 2 tours have real slots**, `00:00`
placeholder included.

The supplier exposes no "has timeslots" flag on the search row. `isTimeslot`
exists only in the `/tours/options` response, which costs one call per tour —
the very call we are trying to avoid. So knowing currently costs the same as
fetching. Exposing that flag on the search row would remove 8 of 10 calls per
search.

Left as-is deliberately: an earlier attempt to skip these calls made Burj
Khalifa report "flexible timing" while 33 real half-hourly slots existed.

---

## Part 2 — The Dhow Cruise price discrepancy

Client screenshot: website Lower Deck **₹2,074.22 per adult**; agent showed
**₹5,630**. Two bugs stacked.

**1. Transfer counted twice.** `optionRate` is keyed on `transferId` and each id
returns a different rate:

```
transferId=3 (Without Transfer) -> 158.62 AED   ticket only
transferId=1 (Sharing)          -> 215.27 AED   ticket WITH sharing bundled in
```

We probed the variant's own tiers first, and for Dhow Marina `own = [1, 3, 3]`,
so **tid=1 won**. We took the transfer-inclusive rate as "Price" and *also*
printed "Sharing Transfer: ₹741" alongside it.

**2. A party total labelled per-person.** `rate` is the whole-party total —
79.31 AED for 1 adult, 158.62 for 2. The tool defaults to `adults=2`, so it
showed the 2-adult total under a column headed "Price".

So ₹5,630 = 215.27 AED = ticket + sharing transfer for 2 adults, where the
website shows one adult's ticket.

Fixed by probing `transferId=3` first (ticket alone, matching how the website
prices it), exposing per-adult and party total separately, and renaming the
column to "Price/adult" with "Transfer (extra)". Now exact:

| Variant | Website | Agent |
|---|---|---|
| Lower Deck | ₹2,074.22 | ₹2,074 per adult |
| Upper Deck | ₹2,370.54 | ₹2,370 per adult |
| Beverage Add-on | ₹1,333.43 | ₹1,333 per adult |

---

## Part 3 — rateCategoryId: the website price gap, settled

The client supplied a token with `rateCategoryId: 1`; ours is `2`. Compared all
352 Dubai tours, same date, same endpoint:

```
tour 540   : ours 525.00  client 550.00   ratio 1.047619
tour 541   : ours  60.564 client  63.448  ratio 1.047619
tour 28488 : ours 178.50  client 187.00   ratio 1.047619
dhow 30551 : ours  75.705 client  79.31   ratio 1.047619
```

**352 of 352 differ, every one by exactly 1.047619 (= 22/21, 4.76%).**

This settles a question open for weeks. The gap is `rateCategoryId` — the
supplier issuing different base rates per account tier. It is **not** ROE and
**not** a markup we apply: ROE is byte-identical on both tokens
(`buyingROE 0.039004588`, `sellingROE 26.1533335203`).

Switching to the client's token would make us match the website and raise every
tour price 4.76%. That is a commercial decision, not a technical one.

Also noted: their token covers Activities, Hotels, Packages, Flight, Transfer,
Visa, Restaurant. Ours lists **Activities only**, yet hotels and flights work —
so a second token is likely in use for those, worth auditing.

---

## Part 4 — Transfer pricing rules (verified, not yet built)

Client's rules, confirmed against live `validateTourOption` data:

```
ticket   = per_adult x actual_pax          (child discounts via price_group)
sharing  = sharing_price x max(actual_pax, minPax)
private  = private_price x ceil(actual_pax / seats_per_vehicle)
total    = ticket + transfer
```

Audited 25 tours / 91 options:

| Sharing (minPax, maxPax) | Options |
|---|---|
| (2, 100) | 21 |
| (2, 12) | 15 |
| (2, 10) | 5 |

Private was `(1, 12)` on all 49 sampled. `vehicleName` is populated on some
(`Toyota Hiace`, `vehicleId: 4`) and null on others.

Worked example, 10 people, private, 12 seats: `ceil(10/12) = 1` vehicle. With a
6-seat vehicle: `ceil(10/6) = 2`, so the 7th passenger adds the second car —
exactly as the client described.

**Open question that must be resolved before shipping the private calculation:**
is `maxPax` on the private row the *vehicle seat count* or a *booking ceiling*?
Sharing rows carry `maxPax: 100`, which cannot be a vehicle — so the field means
"max bookable pax for this transfer type" and merely *coincides* with capacity
for private. If any tour sets private `maxPax: 12` as a ceiling while the car
seats 6, `ceil(pax / maxPax)` returns 1 car instead of 2 and **undercharges**.
Prefer `vehicleName`/`vehicleId` where present; treat `maxPax` as a fallback.

**Display rule:** show sharing and private as indicative per-unit prices until
the customer gives a passenger count — without pax the two are
indistinguishable. Once pax is known, show the computed comparison.

---

## Part 5 — Still open

| Item | Status |
|---|---|
| Infant count accepted explicitly | Not started |
| Capacity by total pax, checked against supplier min/max | Not started |
| `lookup_entity` fetch size (fixes Abu Dhabi ordering) | Not started — two-line fix |
| Cap `get_tour_options` probe at 2, matching `search_tours` | Not started |
| `_tour_price_lookup("Dubai Citytour")` returns `0.0` | **Pre-existing**, confirmed on a clean tree. Itinerary totals silently lose that tour's price |
| `Getoptiondescription` endpoint unused | Tested live, returns 8 sections (Inclusions, Exclusions, Useful Information, Cancellation and Child policies). We never call it, so inclusions are missing from variant replies |
| Departure city asked on hotel searches | Improved from 2/6 to ~1/6; needs a code-level constraint, not prompt wording |
| Duplicate `call_tour_options`, instruction dedupe, ROE cache merge | Production phase |

---

## A note on test hygiene

A full-suite failure this week was a test asserting Burj Khalifa's sharing
transfer costs **₹0**. The supplier now charges **33.99 AED (₹884)**. Live data
changed and the test reported it as a code defect.

Every test touching the live API was swept for hardcoded supplier values. Two
were fragile and now assert the *rule* instead of the number:

- transfer tiers — whichever tiers are free today must survive and read
  "Included"; any paid tier must show a real figure
- restaurant rating — relay whatever the supplier publishes, unrounded (the
  original bug showed 4 when `review.rating` said 4.4)
