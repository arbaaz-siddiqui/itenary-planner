# surfaces/

User-facing entry points. All three wrap the same `agent.py` core.

| File | What it does | How to run |
|---|---|---|
| `streamlit_app.py` | Web chat UI with inline card renderers for flights, hotels, tours, etc. Uses the prompt's `Here are the top X ...:` signal to switch from prose to cards. | `streamlit run surfaces/streamlit_app.py` |
| `whatsapp_app.py` | Twilio webhook receiver. Persists conversation state in SQLite (`WHATSAPP_DB_PATH`). | `uvicorn surfaces.whatsapp_app:app` |
| `mcp_server.py` | Standalone MCP server exposing all 12 booking tools to external MCP clients (Claude Desktop, etc). | `python -m surfaces.mcp_server` |
