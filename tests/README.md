# tests/

| File | Coverage |
|---|---|
| `test_core.py` | Data models, errors, currency conversion, date helpers (43 tests). |
| `test_rules.py` | Pricing, cancellation (supplier-driven), payment schedule, TCS, EMI, currency conversion sanity (66 tests). |
| `test_parsers.py` | All 7 API response parsers against real staging-API fixtures (39 tests). |
| `test_integration.py` | HTTP client behavior + payload shapes match the Postman collection (16 tests). |
| `conftest.py` | Shared fixtures: settings reset, hotel name map. |
| `factories.py` | Builder helpers: `make_budget()`, `make_selection()`, `load_fixture()`. |
| `fixtures/` | Real API responses trimmed to the smallest meaningful subset. Refresh via `python -m scripts.refresh_test_fixtures`. |
