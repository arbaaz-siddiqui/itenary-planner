"""Shared FastMCP server instance.

All MCP tools register themselves on this single instance via @mcp.tool().
The standalone MCP server in surfaces/mcp_server.py runs it.
"""

from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("itinerary_planner")
