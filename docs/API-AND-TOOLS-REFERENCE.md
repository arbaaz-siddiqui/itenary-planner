# Supplier APIs and agent tools — complete reference

Every supplier endpoint we call, what it is for, what goes in, what comes back,
which part of the response we actually use, and the order calls must be made in.

Verified against the live supplier on 2026-09-28. Where a field is described as
unused or empty, that was checked against a real response, not assumed.

---

## 1. Hosts and auth

Two hosts. Which one an endpoint lives on is not a style choice — calling the
wrong one returns 404.

| Host | Constant | Used by |
|---|---|---|
| `https://stagingapi.gujjutours.com` | `BOOKING_BASE_URL` | 24 of 26 endpoints |
| `https://www.gujjutours.com` | `BOOKING_B2C_BASE_URL` | `/api/tours/options`, `/api/tours/optionRate` |

The two B2C tour endpoints are listed in the client's Postman collection under
`stagingb2c.gujjutours.com`, but that host 404s on every `/api` path. They are
also reachable on `stagingapi` under different names
(`TourSearch/TourOptionlist`, `TourSearch/TourOptionRate`) — we use the `www`
spelling.

**Auth:** every call sends `Authorization: Bearer {BOOKING_TOKEN}` plus
`Content-Type`, `Accept`, `X-Requested-With: XMLHttpRequest` and a generated
`X-Trace-Id`. Built by `booking_api/headers.py:base_headers()`.

Hotel static endpoints use `hotel_static_headers()` — a separate
`BOOKING_HOTEL_STATIC_TOKEN` and an `x-accept-language` hint. In our current
config that token is the same value as the main one.

**Retries:** `booking_api/http_client.py` retries 5xx and timeouts with
exponential backoff (max 2 retries), and raises immediately on 4xx. A 500 is
therefore ~3 attempts and several seconds — relevant because some tour variants
500 deterministically.

**Currency:** the supplier quotes in AED (sometimes USD). `fx.convert_supplier_price()`
converts using the ROE pair, cached 5 minutes.

---

## 2. Call order — what must happen before what

### Tours (the deepest chain)

```
1. resolve_city(name)                → city_id, country_id   [local reference data, no HTTP]
2. toursearchlist    (country, city, date)  → tourId, tourName, imagePath
3. toursearchlistrate(country, city, date)  → tourId → price
   └─ 2 and 3 are parsed TOGETHER: a tour with no rate row is dropped as
      unbookable, so neither call alone is enough.
4. /api/tours/options (tourID, travelDate) → optionId, supplierId per variant
5. /api/tours/optionRate (tourID, optionId, supplierId, transferId=3, paxDetails)
   └─ needs optionId + supplierId from step 4. Returns the ticket rate AND
      every transfer tier in one response.
```

Steps 4 and 5 are what `get_tour_options` does. Step 5 is called **once per
variant**, concurrently.

Optional, on demand only:
- `Timeslot` — needs `tourId`, `optionId`, `supplierId`, date (from step 4)
- `Getoptiondescription` — needs the same three ids
- `Tourdetails` — needs only `tourId`

### Hotels

```
1. GetCitiesWithHotel(city_name)     → cityId
2. GetStaticDataByCity(cityId)       → candidate hotelIds (~40 taken)
3. Availabilitywithcancellation(hotelIds, dates, rooms) → rooms + prices
4. GetPropertyDescriptions(hotelId)  → prose, one call PER HOTEL (batching
                                        returns an empty list)
```

### Flights

```
1. resolve IATA (local reference data)
2. /api/Flight/search   → pricedItineraries[]
3. /api/Flight/getflightdetails(fare_source_code) → baggage, fare rules
```

### Transfers

```
1. lookup_entity / geocode → hotel lat,lng + place_id
2. TransferList  → vehicle options + unique_key
3. TransferDetail(unique_key + both coords + both place_ids) → full terms
```

---

## 3. Endpoint reference

### 3.1 Tours

#### `POST /api/v1/tourservices/TourSearch/toursearchlist`
Catalogue for a city and date. **No prices.**

```json
{"countryId": 213, "cityId": 244520, "travelDate": "2026-09-18",
 "tourCategoryId": 1, "lang": "en"}
```

Response `result.tourStaticlists[]`. **Used:** `tourId`, `tourName`,
`imagePath`, `cityName`, `countryName`, `supplierId`, `supplierName`,
`transferScenario`, `cancellationPolicyName`, `duration`, `isRecommanded`.
**Ignored:** `tourrating` and `reviewsCount` are `0` for all 332 Dubai tours —
never rank on them.

`imagePath` is a relative CDN path; `parsers._resolve_image_url()` rewrites it
to `https://d3bfv5x1dw8ekm.cloudfront.net/...`. Tenant-scoped paths
(`/{guid}/TourMedia/...`) need an extra `/uploads` prefix or the CDN 403s.

#### `POST .../toursearchlistrate`
Same payload as above. Response `result[]` keyed by `tourID`. **Used:**
`amount` / `finalAmount`, `currencyCode`, `discountType`. One flat rate per
tour — the sharing/private split is NOT here.

#### `POST /api/tours/options` *(www host)*
Bookable variants of one tour.

```json
{"tourID": 53632, "travelDate": "2026-09-18", "lang": "en"}
```

Response `result.tourOptionlist[]`. **Used:** `optionId`, `optionName`,
`supplierId`, `isTimeslot`, `validateTourOption[]` (min/max pax per transfer
type), `tourRestrictedTransferType[]` (tiers the supplier BLOCKS for this
variant), `restrictedRateType[]` (pax types that cannot book).
**Ignored:** `rate` is always `0` here — prices come from the next call.

No image field of any kind on a variant row; variants inherit the tour image.

#### `POST /api/tours/optionRate` *(www host)* — the pricing call
```json
{"tourID": 53632, "travelDate": "2026-09-18", "optionId": "222565",
 "transferId": 3, "supplierId": 1,
 "paxDetails": [{"label": "adult", "value": "9", "age": ""}],
 "StartTime": "", "guideId": 0, "lang": "en", "timeSlotId": "", "vehicleId": 0}
```

Response `result[0]`:

```json
{"rate": 306.9, "currencyCode": "AED",
 "initialTransferRates": [
   {"transferTypeId": 3, "transferTypeName": "Without Transfer",
    "startingFromRate": 0,   "totalTransferRate": 34.1},
   {"transferTypeId": 1, "transferTypeName": "Sharing Transfer",
    "startingFromRate": 0,   "totalTransferRate": 102.3},
   {"transferTypeId": 2, "transferTypeName": "Private Transfer",
    "startingFromRate": 880, "totalTransferRate": 914.1}]}
```

**This is the single most important response in the system.** How to read it:

- `rate` — ticket TOTAL for the pax in `paxDetails`, not per person.
  9 adults → 306.9 AED. Divide by pax for a per-adult figure.
- `totalTransferRate` — **per person**: the per-head ticket plus that tier's
  transfer. The transfer alone is `totalTransferRate − (the transferId=3 entry)`.
- `startingFromRate` — the **whole-vehicle** private price. Already includes
  the vehicle multiple.

The arithmetic we use:

```
sharing = (sharing.totalTransferRate − without.totalTransferRate) × pax
private = private.startingFromRate                    ← never × pax
```

Two traps, both of which caused live wrong quotes:

1. **`rate` does not always vary by tier.** On Dhow Marina it is 79.31 AED for
   all three transferIds. Deriving the transfer by subtracting whole-ticket
   `rate` values therefore gives 0, and a ₹5,361 private transfer was shown as
   "Included (₹0)". Always use `initialTransferRates`.
2. **`maxPax` is not the seat count.** Desert Safari reports `maxPax: 12` while
   `startingFromRate` doubles (440 → 880) at pax 7 — so the car holds **6** and
   12 is a two-car ceiling. Sizing vehicles from `maxPax` undercharges by a
   whole car.

Above the supplier's ceiling (usually 13+) this endpoint returns **HTTP 500**.
That is a real limit, not a fault. For a larger party we derive the seat count
from where `startingFromRate` steps, then quote `one_car_rate × ceil(pax/seats)`.

#### `POST .../Timeslot`
Needs `tourId`, `optionId`, `supplierId`, `travelDate`. Returns start times
with live `available` seat counts. Cached on the **volatile** tier — a longer
TTL could claim a sold-out slot is open.

#### `POST .../Getoptiondescription`
Needs `tourId`, `tourOptionId`, `supplierId`. Returns `result[]` of
`{type, content, descriptionText}` — Inclusions, Exclusions, Useful
Information, cancellation and child policies. `descriptionText` is HTML;
we extract the text and never forward the markup to the browser.

#### `GET .../Tourdetails?TourId=`
Tour-level content: `duration`, `tourLanguage`, `cancellationPolicyName`,
`transferType[]`, and `descriptions[]` of `{type, content}` — Overview,
Itinerary, Important Information, Terms, FAQ. The "Show details" panel merges
this with `Getoptiondescription` (variant sections first).

### 3.2 Hotels

| Endpoint | Purpose | Key response fields used |
|---|---|---|
| `GetCitiesWithHotel` | city name → `cityId` | `cityId`, `cityName` |
| `GetStaticDataByCity` | candidate hotel ids | `hotelId`, `starRating` |
| `Availabilitywithcancellation` | **prices + rooms** | `hotelId`, room list, `mealName`, `isFreeCancellation`, cancellation policy, amounts |
| `GetHotelStaticDataOptimize` | names, address, coords | `HotelName`, `StarRating`, lat/lng |
| `GetPropertyDescriptions` | prose | `ID`, `Name`, `Description` |
| `GetHotelGuestReview` | ratings | `rating`, `reviewCount` |

`Availabilitywithcancellation` payload takes `hotelIds[]`, `checkIn`,
`checkOut`, `rooms[]` (per-room adults/children/ages), `nationality`.

**Known supplier limits:** 9 rooms, 30 nights, 2 children per room, child ages
1-12, ~12-month booking horizon.

**`GetPropertyDescriptions` is one hotel per call.** `[1350]` returns one
description; `[1350, 1351]` returns zero, and the response does not echo
`hotel_id`, so batching loses the mapping entirely. We fan out one call per
hotel and pair by position.

**No hotel images exist.** `PropertyInfo` returns four fields
(`hotelID`, `HotelName`, `Review`, `StarRating`); `PropertyDescriptions`
returns prose. There is no image endpoint — hotel cards show a placeholder by
necessity, not by bug.

### 3.3 Flights

#### `POST /api/Flight/search`
```json
{"origin": "HYD", "destination": "DXB", "departureDate": "2026-09-19",
 "adults": 2, "cabinClass": "Y", "maxStops": 1, "airlineCode": ""}
```

Response is under **`data.pricedItineraries[]`** — not `result`. Each itinerary
has `originDestinationOptions[].flightSegments[]`.

**Used per segment:** `departureAirportLocationCode`, `arrivalAirportLocationCode`,
`departureDateTime`, `arrivalDateTime`, `journeyDuration`, `marketingAirlineName`,
`flightNumber`, `stopQuantity`, `cabinClass`, baggage.

**Trap: `journeyDuration` is per segment and excludes layovers.** Saudia
HYD→JED→DXB is 340 + 175 = 8h 35m of flying, but the passenger departs 02:25
and lands 19:30 — **17h 5m**. Total duration must be computed as
`last arrival − first departure`, or a one-stop reads like a direct flight.

`airlineCode: ""` returns every carrier — 142 fares across 10 airlines in
~4.7s, which is both faster and more complete than filtering per airline.

Supplier rejects parties above 9 pax.

#### `POST /api/Flight/getflightdetails`
Takes `fare_source_code` from a search result. Returns baggage allowance and
fare rules for that exact fare.

### 3.4 Transfers, restaurants, visas, packages

| Endpoint | Input | Notes |
|---|---|---|
| `TransferList` | date, time, both coords, both place_ids, pax | Returns `unique_key` needed by the detail call |
| `TransferDetail` | `unique_key` + all coords + place_ids | Full terms |
| `/api/restaurant/v1/restaurants` | city, date, pax | Use `review.rating`, not the top-level rating |
| `/api/restaurant/v1/restaurants/{id}` | id | Meal timings, menus |
| `/api/visa/v1/visas` | `nationalityId` **must be 245** for India | Pricing is absent without it |
| `packagelist` / `packagerate` / `packagestaticdata` | city, dates | Rates are a separate call from the list |

#### `GET /api/Currency/ROE/{code}`
Returns `buyingROE` / `sellingROE`. Rule: when the fare currency matches the
account currency divide by `buyingROE`, otherwise multiply by `sellingROE`.
Cached 5 minutes; a single search locks one ROE pair so 300+ conversions cost
one HTTP call.

---

## 4. Agent tools (37)

### Search — one supplier chain each

| Tool | Required args | Cache |
|---|---|---|
| `search_flights` | `origin_city`, `destination_city`, `departure_date` | 3 min |
| `search_hotels` | `destination_city`, `check_in`, `check_out` | 3 min |
| `search_tours` | `destination_city`, `travel_date`, **`adults`** | 3 min |
| `search_airport_transfer_dubai` | `arrival_date` | 3 min |
| `search_restaurants` | `destination_city`, `search_date` | 3 min |
| `list_packages` | — | 3 min |
| `get_visa_info` | `destination_country`, `nationality_country`, `travel_date` | 1 hr |

`adults` is **required** on the tour tools. It used to be optional with a
default, so the model omitted it and the tool guessed the party size by parsing
chat text — which misread "for 6 pp?" and quoted six travellers three-person
transfers. The schema now forces it.

### Detail — need ids from a search

| Tool | Required args |
|---|---|
| `get_tour_options` | `tour_id`, `travel_date`, **`adults`** |
| `get_tour_details` | `tour_id` |
| `get_tour_timeslots` | `tour_id`, `option_id`, `supplier_id`, `travel_date` |
| `get_flight_details` | `fare_source_code` |
| `get_transfer_details` | `unique_key`, both coords, both place_ids, `departure_date` |
| `get_restaurant_details` | `restaurant_id`, `destination_city`, `search_date` |
| `get_package_details` | `package_id` |
| `get_hotel_info` / `get_hotel_description` | `hotel_ids` |
| `get_hotel_reviews` | `hotel_id` |

### Lookup

`lookup_entity` (service + query), `lookup_hotel_city`, `list_city_hotels`,
`list_visa_countries`, `get_exchange_rate`.

`lookup_entity` needs `size >= 40`: at `size=5` it returns 0 Dubai rows where
40 returns 12.

### Calculation — no HTTP, pure arithmetic

`transfer_quote_tool`, `price_group_tool`, `compute_hotel_block_cost_tool`,
`sum_trip_total_tool`, `compute_remaining_budget_tool`, `check_floor_tool`,
`apply_selection_tool`, `compose_customer_payment_summary_tool`,
`resolve_party_tool`, `collect_guest_info_tool`.

### Output

`generate_itinerary_pdf_tool` — writes to `ITINERARY_DIR` and returns
`{itinerary_id, download_url, filename, summary}`. The URL is
`{PUBLIC_BASE_URL}/itinerary/{id}.pdf`, and **the serving route must exist on
whichever service the link points at**. It was missing from `api/main.py`, so
every download 404'd; the Next.js proxy also needs an `/itinerary/*` rewrite.

`display_options_tool`, `build_trip_schedule_tool`, `plan_itinerary_tool`,
`get_destination_tips_tool`.

---

## 5. What we send the model

Search results are trimmed before they reach the agent — the supplier row has
41 fields, the reply needs about a dozen, and the rest are duplicates
(`amenities` and `amenities_display`), always empty, or UI-only.

| Tool | Before | After |
|---|---|---|
| `search_tours` (17 rows) | ~8,270 tokens | ~5,300 |
| `get_tour_options` (24 rows) | ~3,900 | ~3,100 |
| `search_restaurants` | ~2,390 | ~920 |
| `get_visa_info` | ~3,040 | ~590 |

Field lists live in `mcp_tools/slim.py`. Two rules: keep what the **UI** needs
as well as the model (cards are built from the same payload, so ids and image
URLs stay), and prefer the display string over its raw twin.

---

## 6. Field-name contract

The UI (`web/src/components/OptionCards.tsx`) and Streamlit read these names.
Renaming one silently blanks a card.

| Field | Meaning |
|---|---|
| `price_total_inr` | ticket total for `priced_for_pax` people |
| `price_per_adult_inr` | that total ÷ pax |
| `price_display` | rendered per-adult string, or the per-group wording |
| `price_basis` | `per_adult` or `per_group` |
| `transfer_prices[]` | `{transfer_type, price_inr, price_display, priced_for_pax}` |
| `image_url` | CDN URL, on every variant row |
| `is_addon` | needs a main option to book |

A transfer tier that costs nothing is **omitted**, not shown as
"Included (₹0)" — a zero row reads as a free transfer and gives the customer
nothing to act on.

---

## 7. Verified figures

Useful as regression anchors. Checked against the client's own website.

**Desert Safari by Gujjutours, tour 53632, Dune Bashing variant:**

| Pax | Ticket | Sharing | Private | Cars |
|---|---|---|---|---|
| 3 | ₹2,689 | ₹5,379 | ₹11,567 | 1 |
| 6 | ₹5,379 | ₹10,757 | ₹11,567 | 1 |
| 9 | ₹8,068 | ₹16,136 | ₹23,134 | 2 |
| 17 | On request | — | ₹34,701 | 3 |

9 pax + private = **₹31,202**; the website's grand total is ₹31,201.86.

**Dhow Marina Lower Deck, tour 30551, 1 adult:** sharing ₹745, private ₹5,361.
Website shows +744.62 and +5,361.28.

**Private-rate stepping differs per tour** — do not hardcode a seat count:

| Tour | 1–6 pax | 7 | 12 | 13 |
|---|---|---|---|---|
| Desert Safari | 440 | **880** | 880 | 500 error |
| Dhow Marina | 203.94 | 203.94 | 203.94 | 500 error |
| Dhow Creek | 164.29 | 169.95 | **509.85** | — |
| Burj Khalifa | 192.61 | 192.61 | 192.61 | 192.61 |

---

## 8. Open questions for Technoheaven

1. Can `isTimeslot` appear on the `toursearchlist` row? It already exists on
   the option row. Today we make ~15 Timeslot calls to discover that 2 tours
   have slots.
2. Does `GetPropertyDescriptions` support batched ids? `[1350]` works,
   `[1350,1351]` returns nothing — that reads like a bug, and fixing it turns
   5 calls into 1.
3. What are the rate limits, and are they per-token or per-IP? We see
   occasional 429s and are sizing fan-out caps blind.
4. Is there a combined `toursearchlist` + `toursearchlistrate` endpoint? They
   take identical payloads and both sit on every tour search.
5. Why does `optionRate` 500 above 12 pax rather than returning a multi-vehicle
   price? We currently derive vehicle counts ourselves.
6. `maxPax` reports 12 where the vehicle holds 6. Is there a field for the real
   seat count?
