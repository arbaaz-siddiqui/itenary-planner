"""mcp_tools — One file per MCP-exposed tool (extensibility surface).

Each tool function carries BOTH @tool (for LangChain agent) AND
@mcp.tool() (for external MCP clients). Same logic, two protocols.
"""

import sys as _sys
from pathlib import Path as _Path

# Ensure the project root is always importable regardless of which directory
# Streamlit (or any other surface) is launched from.
_root = str(_Path(__file__).resolve().parent.parent)
if _root not in _sys.path:
    _sys.path.insert(0, _root)
