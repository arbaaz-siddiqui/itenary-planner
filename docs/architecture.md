# Architecture

41 files. Strict top-down imports. Bottom layers are pure functions.

## Layout

```
itinerary_planner_v2_lean/
│
├── core.py                  ← Models, errors, currency, dates (no project imports)
├── settings.py              ← Pydantic Settings classes per domain
├── parsers.py               ← 7 response parsers (pure)
├── rules.py                 ← Pricing + policies + budget (pure)
├── reference_data_loader.py ← JSON loaders
├── llm.py                   ← LLM provider factory
├── agent.py                 ← LangGraph agent + logging + checkpoints
├── agent_tools.py           ← Plain tools + ALL_TOOLS registry
│
├── booking_api/             ← HTTP boundary (3 files)
├── mcp_tools/               ← 7 search tools, one per file
├── surfaces/                ← 3 entry points (streamlit, whatsapp, mcp)
├── tests/                   ← 5 test files + JSON fixtures
├── prompts/                 ← Versioned markdown prompts
├── reference_data/          ← Cities + hotel master lists
├── scripts/                 ← diagnose_apis, grade_benchmarks
└── docs/                    ← This file
```

## Layers and import direction

```
surfaces/  →  agent.py + agent_tools.py
              ↓
      mcp_tools/ + booking_api/
              ↓
      parsers.py + rules.py + reference_data_loader.py + llm.py
              ↓
      settings.py
              ↓
            core.py          (imports nothing project-internal)
```

Lower layers never import from higher layers.

## Where to look when X breaks

| Symptom                              | File                                            |
| ------------------------------------ | ----------------------------------------------- |
| Flight prices wrong                  | `parsers.py` (parse_flight_response)            |
| Hotel search returns 401             | `booking_api/endpoints.py` (call_hotel_availability) + `.env` token |
| Hotel returns "no record found"     | `reference_data/hotels/dubai.json` (hotel IDs)  |
| Agent tone wrong                     | `prompts/system_prompt_v1.md`                   |
| WhatsApp messages too long           | `surfaces/whatsapp_app.py` (format_for_whatsapp)|
| Budget calc wrong                    | `rules.py` (BUDGET section)                     |
| Child discount wrong                 | `rules.py` (CHILD_AGE_TIERS)                    |
| Env var not read                     | `settings.py`                                   |
| Card not rendering in Streamlit      | `surfaces/streamlit_app.py` (CARD_TRIGGER_RE)   |
| Currency conversion off              | `core.py` (to_inr) + `settings.py` (CurrencySettings) |
| LLM responses are weird              | `prompts/system_prompt_v1.md` and `.env` model  |

## Pure vs impure

| Module                       | Pure? | Tests |
| ---------------------------- | ----- | ----- |
| `core.py`                    | ✓     | unit  |
| `parsers.py`                 | ✓     | unit, fixture-based |
| `rules.py`                   | ✓     | unit  |
| `reference_data_loader.py`   | I/O (cached) | unit |
| `booking_api/`               | I/O   | integration with mocked HTTP |
| `llm.py`                     | I/O   | (not unit-tested) |
| `agent.py`, `agent_tools.py` | I/O   | smoke |
| `surfaces/`                  | I/O   | (manual) |

## Adding a new tool

Five edits:

1. **`booking_api/endpoints.py`** — add `call_<thing>()` function
2. **`core.py`** — add the new Pydantic model (if needed)
3. **`parsers.py`** — add `parse_<thing>_response()`
4. **`mcp_tools/<thing>.py`** — create the file with `_impl` + both decorators
5. **`agent_tools.py`** — add import + entry in `_build_all_tools()`

Then add tests:
- `tests/fixtures/<thing>_response.json`
- A test class in `tests/test_parsers.py`
- A test in `tests/test_integration.py`

## Adding a new surface

Just create one file in `surfaces/`. Import from `agent.py` and call
`build_react_agent(surface="<name>")`. Don't import from another surface.

## CLIENT_PLACEHOLDER index

Search the repo: `grep -r CLIENT_PLACEHOLDER`

| File                                  | Item                                   |
| ------------------------------------- | -------------------------------------- |
| `rules.py` (PRICING section)          | child age tiers, infant fee, group/peak/markup/GST/tourism dirham |
| `rules.py` (POLICIES section)         | airport routing thresholds, restaurant lists, visa price+docs, handoff thresholds + contacts |
| `agent_tools.py` (DESTINATION_TIPS)   | static destination tips                |
| `reference_data/hotels/dubai.json`    | hotel master list (only IDs 206, 509 confirmed real) |
| `prompts/system_prompt_v1.md`         | tone, addressing style                 |
| `prompts/whatsapp_addendum.md`        | WhatsApp-specific phrasing             |

**Resolved** (no longer placeholders, per client direction 2026-05):

| Item                  | Resolution                              |
| --------------------- | --------------------------------------- |
| Cancellation tiers    | Dropped hardcoded `CANCELLATION_TIERS`. Now read from supplier API via `parse_supplier_cancellation_terms()` in `rules.py`. |
| Payment terms         | Dynamic via `compute_payment_schedule()` — derives from cancellation cutoff + safety buffer. |
| TCS (Indian compliance) | `compute_tcs()` implements Section 206C(1G): 20% on overseas tour packages, 5% above ₹7L on non-package overseas remittance. |
| EMI                   | `compute_emi_options()` surfaces 3/6/9/12-month tenures. Real gateway integration deferred to checkout phase. |
| GST line items        | Skipped per client direction — included silently in totals, never itemized to customer. |

## Dynamic pricing — how it works

Per client direction (Tanvir, 2026-05), the customer should see ONE inclusive
INR total during exploration and a clear payment SCHEDULE at booking time.
**No per-component breakdown, no GST/TCS line items, no supplier-level rules.**

The agent calls `compose_customer_payment_summary_tool` when the user is
ready to book. It returns:
- `total_inr_inclusive` — one number (taxes/fees baked in)
- `payment_schedule` — list of installments with labels and ISO dates
- `emi_starting` — "₹X/month over N months" hint
- `compliance_documents` — e.g. ["PAN Card", "Passport"] for international
- `disclaimer` — "All taxes and fees included."

Internal calculation flow:

1. **Cancellation cutoff** = earliest `is_free_cancellation` window from all
   booked services' `CancellationPolicy[]` (per supplier API). Function:
   `parse_supplier_cancellation_terms()`.
2. **Customer payment cutoff** = supplier cutoff − `PAYMENT_BUFFER_DAYS` (3).
3. **Bucket** based on days-until-travel:
   - `>120 days` → `DEPOSIT_PCT_120PLUS` (20%) deposit + balance later
   - `30-120 days` → `DEPOSIT_PCT_30_120` (50%) deposit + balance
   - `<30 days` → `DEPOSIT_PCT_30` (100%) — full payment now
4. **TCS** computed but not itemized to customer (used for compliance docs only).
5. **EMI** = `total / max_tenure` as a hint (real gateway gives real numbers).

All thresholds live in `PricingSettings` and are overridable via `.env`.

## Known API state

The client supplied a Postman collection (`N8N-Technoheven V1`) with 14
endpoints against `https://stagingapi.gujjutours.com`. Authentication is
via a JWT token in the `Authorization` header.

All 14 endpoints have been hit against staging (`scripts/sample_all_apis.py`)
and return HTTP 200. Parser shapes are derived from real responses in
`/mnt/user-data/uploads/api_samples.json`; refresh with
`python -m scripts.refresh_test_fixtures` whenever the API drifts.

| Endpoint                | Path                                                          | Method | Verified | Notes |
| ----------------------- | ------------------------------------------------------------- | ------ | -------- | ----- |
| FlightSearch            | `/api/Flight/search`                                          | POST   | ✓ 156 itineraries | uses flight_search tenant; **API returns wrong-destination results and bogus ₹270 INR fares** — parser filters both |
| FlightDetails           | `/api/Flight/getflightdetails`                                | POST   | ✓ error shape known | uses flight_list tenant + custom-host header |
| HotelSearch             | `/api/xconnect/Availabilitywithcancellation`                  | POST   | ✓ 2 hotels | account tenant; **no `StarRating` field** — defaults to 0 |
| TourList                | `/api/v1/tourservices/TourSearch/toursearchlist`              | POST   | ✓ 197 tours | account tenant; descriptions empty here |
| TourListrate            | `/api/v1/tourservices/TourSearch/toursearchlistrate`          | POST   | ✓ 175 rates | parser drops 26 tours with no rate |
| TourDetails             | `/api/v1/tourservices/TourSearch/Tourdetails?TourId=N`        | GET    | ✓ null for invalid id | account tenant |
| TransferList            | `/api/transferservices/TransferList`                          | POST   | ✓ body statusCode=404 (no transfers for sample coords) | account tenant |
| TransferDetails         | `/api/transferservices/TransferDetail` (singular!)            | POST   | ✓ shape known | account tenant |
| RestaurantList          | `/api/restaurant/v1/restaurants`                              | POST   | ✓ 4 restaurants | account tenant |
| RestaurantDetails       | `/api/restaurant/v1/restaurants/{id}`                         | POST   | ✓ shape known | account tenant |
| Visa                    | `/api/visa/v1/visas`                                          | POST   | ✓ nested visas[*].options[*] | parser flattens to one row per (visa × option) |
| PackageList             | `/api/staticpackageservices/staticpackage/packagelist`        | POST   | ✓ 31 packages | pricing now in list endpoint (totalPrice/currencyName) |
| PackageRate             | `/api/staticpackageservices/staticpackage/packagerate`        | POST   | ✓ {package, packageHotel, packageAvailability} | per-package only |
| PackageStaticData       | `/api/staticpackageservices/staticpackage/packagestaticdata?PackageId=N` | GET | ✓ shape known | account tenant |

### Response envelopes

Most endpoints wrap responses in `{statusCode, error, result, elapsedTime}`.
Flight uses `{success, isCompleted, data, error[]}`. Hotel uses
`{AvailabilityRS, Error[]}`. **The body-level `statusCode` can disagree
with the HTTP status** — e.g. TransferList returns HTTP 200 with body
`{statusCode: 404, result: []}` when no transfers match. The http_client
logs a warning on such soft errors.

### Real-world data quality issues found

The staging API returns some data that should be filtered before showing
to a user. The parsers handle these:

- **Flight: ~25% of itineraries are wrong-destination** — searching
  DEL→BOM returns DEL→NMI (Saipan) etc. as related-route suggestions.
  Parser drops them via `expected_destination=` filter.
- **Flight: Air India INR-labeled fares of ₹269 for DEL→BOM** —
  probably supplier-side admin/test data, not real bookable prices.
  Parser drops anything below `FLIGHT_PRICE_INR_FLOOR = ₹2000`.
- **Hotel: no `StarRating` field in response** — defaults to 0; UI
  should hide stars rather than show "0 star".
- **Tour: 26 of 197 tours have no matching rate** — parser drops them
  rather than returning `price=0` (which would sort to the top).
- **Package: many packages have `totalPrice=0`** — legitimate
  "on request" pricing; parser sets `pricing_available=False` and
  sorts these after priced packages.

To refresh actual response samples and check live status:
```bash
python -m scripts.diagnose_apis           # full probe with detail follow-ups
python -m scripts.diagnose_apis --quick   # search/list endpoints only
python -m scripts.diagnose_apis --only flight,hotel
```

Output lands in `data/samples/<NAME>.json`. Each file is one endpoint's
raw response. `RESPONSES_SUMMARY.json` is the at-a-glance health snapshot.

### Token rotation

The client's JWT expires **2027-05-25**. After that date, every call
returns 401 until a fresh token is set in `BOOKING_TOKEN`.

### Three tenant IDs

The Postman samples reveal three distinct tenant IDs:

| Tenant                        | Used by                          | Env var                  |
| ----------------------------- | -------------------------------- | ------------------------ |
| Account                       | Hotels, Tours, Transfers, Restaurants, Visa, Packages | `BOOKING_TENANT_ID`       |
| Flight-list (getflightdetails)| FlightDetails only               | `FLIGHT_LIST_TENANT_ID`  |
| Flight-search                 | FlightSearch only                | `FLIGHT_SEARCH_TENANT_ID`|

If Technoheaven rotates them, override via `.env` — no code change.

## Debugging quickstart

```bash
# Check every endpoint (saves raw responses to data/samples/)
python -m scripts.diagnose_apis

# Just inspect what was sent + summaries (no detail calls)
python -m scripts.diagnose_apis --quick

# Inspect one saved sample
cat data/samples/FLIGHT_SEARCH.json | python -m json.tool | less

# Quick settings check
python -c "from settings import get_booking_api_settings; print(get_booking_api_settings())"

# Quick agent test
python -c "from agent import build_react_agent; a = build_react_agent(); print(a.invoke({'messages':[{'role':'user','content':'hello'}]}, {'configurable':{'thread_id':'test'}}))"
```

## Error taxonomy

All errors inherit from `TripPlannerError`. When you see one in logs:

| Error class                | Meaning                                      |
| -------------------------- | -------------------------------------------- |
| `BookingApiUnauthorized`   | 401 from Technoheaven (token/permission)     |
| `BookingApiNotFound`       | 404 (endpoint missing or no record)          |
| `BookingApiTimeout`        | Request timed out after retries              |
| `BookingApiServerError`    | 5xx after retries                            |
| `FlightSearchFailed`       | Anything that broke during flight search     |
| `FlightDetailsFailed`      | Anything that broke during getflightdetails  |
| `HotelSearchFailed`        | Anything that broke during hotel availability|
| `TourSearchFailed`         | Tour list or rate call failed                |
| `TourDetailsFailed`        | Tour-details GET call failed                 |
| `TransferSearchFailed`     | Transfer list failed                         |
| `TransferDetailsFailed`    | Transfer details failed                      |
| `RestaurantSearchFailed`   | Restaurant list failed                       |
| `RestaurantDetailsFailed`  | Restaurant details failed                    |
| `VisaInfoFailed`           | Visa list/details failed                     |
| `PackageSearchFailed`      | Package list or rate failed                  |
| `PackageDetailsFailed`     | Package static-data failed                   |
| `FlightNormalizationError` | Parser couldn't make sense of the JSON       |
| `InvalidDateError`         | Bad date format or out-of-range              |
| `OverBudgetError`          | User tried to exceed their stated budget     |
| `MissingApiKey`            | Required env var is empty                    |

Each carries structured fields. Always log `error.to_dict()`.
