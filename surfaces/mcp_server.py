"""Stand-alone MCP server.

Run for Claude Desktop or other external MCP clients:
    python -m surfaces.mcp_server

Importing the tool modules registers them via @mcp.tool() decorators.
"""

from __future__ import annotations

# Detail tools
import mcp_tools.get_flight_details
import mcp_tools.get_package_details
import mcp_tools.get_restaurant_details
import mcp_tools.get_tour_details
import mcp_tools.get_transfer_details

# Search/list tools
import mcp_tools.get_visa_info
import mcp_tools.list_packages
import mcp_tools.search_flights
import mcp_tools.search_hotels
import mcp_tools.search_restaurants
import mcp_tools.search_tours
import mcp_tools.search_transfers  # noqa: F401
from agent import configure_logging

# Imports below have side effects (decorator registration) — order matters
from mcp_tools.server import mcp


def main() -> None:
    configure_logging(prod=False)
    mcp.run()


if __name__ == "__main__":
    main()
