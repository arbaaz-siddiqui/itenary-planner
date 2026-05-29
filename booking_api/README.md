# booking_api/

HTTP boundary to the Technoheaven / Gujju Tours backend. Owns auth headers,
retries, error mapping. NEVER parses responses — that's `parsers.py`.

| File | What it does |
|---|---|
| `endpoints.py` | One function per API endpoint (14 total: 9 search/list + 5 detail). Builds payload, calls HTTP client, returns raw JSON. |
| `headers.py` | Header builders: `base_headers()`, `flight_search_headers()`, `flight_list_headers()`. Injects the 3 tenant IDs from settings. |
| `http_client.py` | Retry-wrapped HTTP client. Maps HTTP status to typed errors (`BookingApiUnauthorized`, `NotFound`, etc). Logs soft-errors when body `statusCode >= 400` despite HTTP 200. |
| `__init__.py` | Re-exports the 14 `call_*` functions. |
