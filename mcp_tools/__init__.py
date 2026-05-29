"""mcp_tools — One file per MCP-exposed tool (extensibility surface).

Each tool function carries BOTH @tool (for LangChain agent) AND
@mcp.tool() (for external MCP clients). Same logic, two protocols.
"""
