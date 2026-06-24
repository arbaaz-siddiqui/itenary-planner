# AI Itinerary Planner — Agent Flow & Current State

This document describes what the system does today: the surfaces, the LangGraph
ReAct agent, the tools it can call, and the underlying Technoheaven (Gujju Tours)
booking APIs each tool hits.

---

## 1. High-level architecture

```mermaid
flowchart TB
    subgraph Surfaces["User Surfaces"]
        ST["Streamlit Web App<br/>(chat + API/Tool Inspector + PDF download)"]
        WA["WhatsApp Bot<br/>(Twilio webhook + PDF media delivery)"]
    end

    subgraph Core["Agent Core"]
        AG["LangGraph ReAct Agent<br/>(create_react_agent)"]
        LLM["LLM Provider<br/>OpenRouter / Anthropic / Self-hosted"]
        SP["System Prompt v1<br/>(anti-fabrication rules)"]
        CP["Checkpointer<br/>(in-memory / SQLite per thread)"]
    end

    subgraph Tools["Tool Layer (ALL_TOOLS)"]
        SEARCH["Search & Detail Tools"]
        CALC["Deterministic Calc Tools<br/>(party, budget, totals)"]
        OUT["Output Tools<br/>(display cards, PDF)"]
    end

    subgraph Data["Booking & Data Layer"]
        BAPI["Technoheaven / Gujju Tours API<br/>(B2B + B2C hosts)"]
        FX["Live ROE (AED to INR)"]
        REF["Reference Data<br/>(cities, curated hotels, airport)"]
    end

    ST --> AG
    WA --> AG
    AG <--> LLM
    SP --> AG
    CP <--> AG
    AG <--> Tools
    SEARCH --> BAPI
    SEARCH --> FX
    SEARCH --> REF
    CALC -.no API.-> AG
    OUT --> ST
    OUT --> WA
```

---

## 2. Per-turn ReAct loop (what happens on each user message)

```mermaid
flowchart TD
    U["User message<br/>(web or WhatsApp)"] --> STREAM["stream_and_log()<br/>token streaming + tool trace"]
    STREAM --> AGENT["Agent node (LLM)<br/>reads system prompt + history"]

    AGENT --> DECIDE{"Needs a tool?"}
    DECIDE -->|"No — just reply"| REPLY["Stream assistant text<br/>token-by-token"]
    DECIDE -->|"Yes"| TOOLCALL["Emit tool call<br/>(name + args)"]

    TOOLCALL --> TOOLNODE["Tool executes"]
    TOOLNODE --> APICALL{"Hits booking API?"}
    APICALL -->|"Yes"| HTTP["HTTP request<br/>(recorded for Inspector)"]
    APICALL -->|"No — calc only"| LOCAL["Local computation"]
    HTTP --> PARSE["Parser normalizes<br/>+ converts AED to INR (live ROE)"]
    LOCAL --> RESULT
    PARSE --> RESULT["Structured tool result"]

    RESULT --> AGENT
    REPLY --> LOG["log_turn()<br/>Excel benchmark row"]
    LOG --> DONE["Response + tool trace<br/>shown in UI"]

    DONE --> CARDS{"display_options called?"}
    CARDS -->|"Yes"| RENDER["Render visual cards<br/>(images, prices)"]
    CARDS -->|"No"| END["Text reply only"]
    DONE --> PDF{"generate PDF called?"}
    PDF -->|"Yes"| PDFOUT["Branded PDF<br/>(download link / WhatsApp media)"]
```

---

## 3. Tool inventory (grouped by what they do)

```mermaid
flowchart LR
    subgraph SearchTools["Search / Discovery"]
        sf["search_flights"]
        sh["search_hotels"]
        stt["search_tours"]
        str["search_airport_transfer_dubai"]
        sr["search_restaurants"]
        gv["get_visa_info"]
        lp["list_packages"]
        gx["get_exchange_rate"]
    end

    subgraph DetailTools["Detail / Drill-down"]
        gfd["get_flight_details"]
        gtd["get_tour_details"]
        gto["get_tour_options"]
        gtod["get_tour_option_details"]
        gtrd["get_transfer_details"]
        grd["get_restaurant_details"]
        gpd["get_package_details"]
    end

    subgraph HotelTools["Hotel Static Content"]
        lhc["lookup_hotel_city"]
        lch["list_city_hotels"]
        ghi["get_hotel_info"]
        ghd["get_hotel_description"]
        ghr["get_hotel_reviews"]
    end

    subgraph CalcTools["Deterministic Calc (no API)"]
        cgi["collect_guest_info"]
        rp["resolve_party"]
        cf["check_floor"]
        pg["price_group"]
        chbc["compute_hotel_block_cost"]
        stot["sum_trip_total"]
        as_["apply_selection"]
        crb["compute_remaining_budget"]
        gdt["get_destination_tips"]
    end

    subgraph OutputTools["Output"]
        do["display_options (cards)"]
        cps["compose_customer_payment_summary"]
        pdf["generate_itinerary_pdf"]
    end
```

---

## 4. Booking API endpoints actually called today

```mermaid
flowchart LR
    subgraph B2B["B2B host — stagingapi.gujjutours.com"]
        F["/api/Flight/search<br/>(flights)"]
        HA["/api/xconnect/Availabilitywithcancellation<br/>(hotel availability + price)"]
        HS["/api/xconnect/GetHotelStaticDataOptimize<br/>(hotel names / static)"]
        HC["/api/xconnect/GetStaticDataByCity<br/>+ checkhotel (discover hotel IDs)"]
        HR["/api/xconnect/GetHotelGuestReview<br/>(reviews)"]
        TS["/api/v1/tourservices/.../toursearchlist(+rate)<br/>(tours + pricing)"]
        TR["/api/transferservices/TransferList<br/>(airport transfers)"]
        V["/api/visa/v1/visas<br/>(visa info)"]
        ROE["/api/Currency/ROE/INR<br/>(live AED to INR rate)"]
    end

    subgraph B2C["B2C host — stagingb2c.gujjutours.com"]
        TO["Tour options / timeslots"]
        TPC["Tour price calendar"]
        TOD["Tour option details"]
    end

    style TR fill:#ffe0e0,stroke:#c00
    style B2C fill:#fff4e0
```

> **Note on transfers (red box):** `TransferList` returns `HTTP 200` only when
> given real Google Place IDs for pickup/drop. Reference data currently uses
> placeholder IDs (`"DXB"` / `"HOTEL"`), so live calls return `404` and the tool
> yields **0 options**. This is the open item flagged in the client email — we
> need the supplier's correct location-ID/Place-ID contract for transfers.

---

## 5. What is built and working today

| Area | Status | Notes |
|------|--------|-------|
| Streamlit web chat | ✅ Working | Streaming replies + API/Tool Inspector (shows every tool + raw HTTP call) |
| WhatsApp bot | ✅ Working | Twilio webhook; delivers PDF as media |
| Flights | ✅ Working | Live search; INR conversion; bogus-fare filter |
| Hotels | ✅ Working | Live discovery of real Dubai inventory + real names + availability pricing |
| Hotel info/reviews/description | ✅ Working | Real address, coords, guest reviews from supplier |
| Tours / activities | ✅ Working | Live search + per-adult pricing; tour options & details |
| Visa | ✅ Working | Live visa info |
| Live ROE (AED→INR) | ✅ Working | Real rate from Currency API, 10-min cache |
| Branded PDF itinerary | ✅ Working | Gujju Tours letterhead; web download + WhatsApp media |
| Budget / party / totals math | ✅ Working | Deterministic tools (no LLM math) |
| **Airport transfers** | ⚠️ **Blocked** | API returns 404 — needs correct Place-ID/location contract from supplier |
| **Booking / payment** | ❌ **Not built** | No booking API integrated yet — needs the supplier's booking flow (see email) |
| Anti-fabrication | ⚠️ Partial | Prompt guardrails in place; current LLM (Mistral) still occasionally invents hotel/flight detail — see Sarvam AI ask |

---

## 6. Known gaps / decisions pending the client

1. **Transfers** — need the supplier's real contract for pickup/drop location
   identifiers (Google Place ID vs. their own location ID).
2. **Booking flow** — the planner produces quotes & itineraries but does **not**
   yet place a booking. We need the supplier's booking API sequence
   (hold → confirm → pay → voucher), required fields, and payment handling.
3. **LLM quality** — the current model fabricates detail under pressure.
   Requesting **Sarvam AI** access to evaluate it as the production model
   (better Indian-English/Hindi handling; provider swap only, no rearchitecture).
