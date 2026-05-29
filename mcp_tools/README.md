# mcp_tools/

Each file is ONE tool, exposed two ways: as a LangChain `@tool` for the agent
and as a FastMCP endpoint for external MCP clients. Pattern: build payload →
call `booking_api` → parse via `parsers.py` → return dict.

| File | Tool name | What it does |
|---|---|---|
| `server.py` | — | FastMCP app instance shared by all tools. |
| `search_flights.py` | `search_flights` | Round-trip or one-way flight search with IATA-based destination filter. |
| `search_hotels.py` | `search_hotels` | Hotel availability with per-room cancellation and pricing. |
| `search_tours.py` | `search_tours` | Dubai tour/activity list with rates. |
| `search_transfers.py` | `search_airport_transfer_dubai` | Point-to-point transfers via coordinates. |
| `search_restaurants.py` | `search_restaurants` | Restaurant list with cuisine and veg-type filters. |
| `get_visa_info.py` | `get_visa_info` | UAE visa options for Indian passport holders. |
| `list_packages.py` | `list_packages` | Pre-built Dubai holiday packages. |
| `get_flight_details.py` | `get_flight_details` | Full fare rules for a specific `fareSourceCode`. |
| `get_tour_details.py` | `get_tour_details` | Full description / inclusions / exclusions for one tour. |
| `get_transfer_details.py` | `get_transfer_details` | Amenities and capacity for one transfer. |
| `get_restaurant_details.py` | `get_restaurant_details` | Menu, timings, full review for one restaurant. |
| `get_package_details.py` | `get_package_details` | Itinerary + media for one package. |
