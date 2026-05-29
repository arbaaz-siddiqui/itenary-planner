# Itinerary Planner v2

Dubai trip planner. Streamlit web UI + WhatsApp bot, backed by the
ActivityLinker booking API and a LangGraph ReAct agent.

## Quick start

```bash
# 1. Environment
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 2. Configuration
cp .env.example .env
nano .env                            # fill in credentials

# 3. Pre-commit hooks (one-time)
pre-commit install

# 4. Tests
pytest

# 5. Pick a surface
streamlit run surfaces/streamlit_app.py
uvicorn surfaces.whatsapp_app:app --reload --port 8000
python -m surfaces.mcp_server
```

## Codebase map

41 files. Every file does ONE thing.

```
itinerary_planner_v2_lean/
│
├── core.py                       # Pydantic models, errors, currency, dates
├── settings.py                   # All Pydantic Settings classes
├── parsers.py                    # All 7 API response parsers (pure functions)
├── rules.py                      # Pricing rules + client policies + budget math
├── reference_data_loader.py      # Cities + hotels JSON loaders
├── llm.py                        # LLM provider factory (OpenRouter / Anthropic / Qwen)
├── agent.py                      # LangGraph ReAct agent + response extractors
├── agent_tools.py                # Plain tools (intake, budget_ops, travel_info) + registry
│
├── booking_api/
│   ├── http_client.py            # HTTP session + retries + error mapping
│   ├── headers.py                # Auth + tenant + intra-secret headers
│   └── endpoints.py              # 7 endpoint functions
│
├── mcp_tools/                    # One file per MCP-exposed tool (extensibility)
│   ├── server.py                 # Shared FastMCP instance
│   ├── search_flights.py
│   ├── search_hotels.py
│   ├── search_tours.py
│   ├── search_transfers.py
│   ├── search_restaurants.py
│   ├── get_visa_info.py
│   ├── list_packages.py
│   ├── get_flight_details.py     # detail follow-ups
│   ├── get_tour_details.py
│   ├── get_transfer_details.py
│   ├── get_restaurant_details.py
│   └── get_package_details.py
│
├── surfaces/                     # Three independent entry points
│   ├── streamlit_app.py          # Web UI (chat + sidebar + cards inline)
│   ├── whatsapp_app.py           # FastAPI + Twilio + formatter
│   └── mcp_server.py             # Standalone MCP server (Claude Desktop)
│
├── tests/                        # Mirror source where useful
│   ├── conftest.py               # Fixtures + factories
│   ├── test_core.py              # currency, dates, errors, models
│   ├── test_parsers.py           # All 7 parsers using JSON fixtures
│   ├── test_rules.py             # Pricing + policies + budget
│   ├── test_integration.py       # booking_api with mocked HTTP
│   └── fixtures/                 # Sample API responses
│
├── prompts/
│   ├── system_prompt_v1.md
│   └── whatsapp_addendum.md
│
├── reference_data/
│   ├── cities.json
│   └── hotels/dubai.json         # CLIENT_PLACEHOLDER
│
├── scripts/
│   ├── diagnose_apis.py          # End-to-end API health check
│   └── grade_benchmarks.py       # Excel benchmark grader
│
├── docs/
│   └── architecture.md           # Architecture + naming + debugging in one doc
│
├── pyproject.toml
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── render.yaml
└── README.md
```

## Where to look when X breaks

| Symptom                              | File                                            |
| ------------------------------------ | ----------------------------------------------- |
| Flight prices wrong                  | `parsers.py` (`parse_flight_response`) or `rules.py` |
| Hotel search returns 401             | `booking_api/endpoints.py` (`call_hotel_availability`) |
| Agent tone off                       | `prompts/system_prompt_v1.md`                   |
| WhatsApp messages too long           | `surfaces/whatsapp_app.py` (`format_for_whatsapp`) |
| Budget calc wrong                    | `rules.py` (budget section)                     |
| Env var not read                     | `settings.py`                                   |
| Card not rendering                   | `surfaces/streamlit_app.py` (`render_*_card`)   |

## Architecture in one paragraph

Strict top-down imports: surfaces → agent + tools → business logic (parsers, rules)
→ booking_api + llm → core (models, errors). `core.py` imports nothing from this
project. The bottom layers are pure functions. The top layers are thin.

## Pending items

Search for `CLIENT_PLACEHOLDER` to find every value awaiting the client's
questionnaire response. See `docs/architecture.md` for the full list.
