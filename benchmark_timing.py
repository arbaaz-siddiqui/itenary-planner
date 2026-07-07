"""benchmark_timing.py — times every step of a real agent turn.

Run:
    python benchmark_timing.py

Prints a table showing:
  - LLM think time (user message → tool call decision)
  - Per API call duration (from the http_client recorder)
  - LLM format time (tool results → final answer)
  - Total wall-clock time
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent import build_react_agent, extract_assistant_text, configure_logging
from booking_api.http_client import get_http_request_log, latest_http_seq

configure_logging(level="WARNING")  # suppress noise, we print our own

QUERY = "I need a flight from Mumbai to Dubai on 3rd August 2026, returning 7th August, 1 adult"
THREAD_ID = "benchmark-001"

SEP = "-" * 70


def fmt_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{ms/1000:.2f}s"
    return f"{ms:.0f}ms"


async def run():
    print(SEP)
    print(f"  QUERY: {QUERY}")
    print(SEP)

    agent = build_react_agent(surface="streamlit")
    config = {"configurable": {"thread_id": THREAD_ID}}

    seq_before = latest_http_seq()
    t_total_start = time.perf_counter()

    # Stream the agent so we can capture exact timestamps at each event
    t_llm_start = time.perf_counter()
    t_first_tool_call = None
    t_tools_done = None
    t_final_answer = None

    events = []
    async for event in agent.astream(
        {"messages": [{"role": "user", "content": QUERY}]},
        config=config,
        stream_mode="updates",
    ):
        now = time.perf_counter()
        for node, data in event.items():
            msgs = (data or {}).get("messages", [])
            for m in msgs:
                role = getattr(m, "type", None) or getattr(m, "role", None)
                if role in ("ai", "assistant"):
                    tool_calls = getattr(m, "tool_calls", []) or []
                    content = getattr(m, "content", "") or ""
                    if tool_calls:
                        if t_first_tool_call is None:
                            t_first_tool_call = now
                        for tc in tool_calls:
                            events.append({
                                "event": "LLM_TOOL_CALL",
                                "tool": tc.get("name", "?"),
                                "args": tc.get("args", {}),
                                "t": now,
                            })
                    elif content and content.strip():
                        t_final_answer = now
                        events.append({
                            "event": "LLM_FINAL_ANSWER",
                            "content": content,
                            "t": now,
                        })
                elif role == "tool":
                    if t_tools_done is None or now > t_tools_done:
                        t_tools_done = now
                    events.append({
                        "event": "TOOL_RESULT",
                        "tool": getattr(m, "name", "?"),
                        "t": now,
                    })

    t_total_end = time.perf_counter()

    # --- HTTP log ---
    http_calls = [r for r in get_http_request_log() if r["seq"] > seq_before]

    # --- Print results ---
    print()
    print("  TIMING BREAKDOWN")
    print(SEP)

    llm_think_ms = ((t_first_tool_call or t_total_end) - t_llm_start) * 1000
    print(f"  LLM think (query → tool decision):   {fmt_ms(llm_think_ms)}")

    if http_calls:
        print()
        print("  API CALLS (from http_client recorder):")
        total_api_ms = 0.0
        for r in http_calls:
            d = r["duration_ms"]
            total_api_ms += d
            status = r.get("status_code") or "ERR"
            path = r["url"].replace("https://stagingapi.gujjutours.com", "")
            print(f"    [{status}] {r['method']} {path}")
            print(f"           duration: {fmt_ms(d)}")
        print(f"  Total API time (sequential sum):      {fmt_ms(total_api_ms)}")
    else:
        print("  (no API calls recorded — check BOOKING_TOKEN in .env)")

    if t_tools_done and t_final_answer:
        llm_format_ms = (t_final_answer - t_tools_done) * 1000
        print()
        print(f"  LLM format (results → final answer):  {fmt_ms(llm_format_ms)}")

    total_ms = (t_total_end - t_total_start) * 1000
    print()
    print(SEP)
    print(f"  TOTAL WALL-CLOCK:                      {fmt_ms(total_ms)}")
    print(SEP)

    # --- Final answer ---
    print()
    print("  AGENT RESPONSE:")
    print(SEP)
    for ev in events:
        if ev["event"] == "LLM_FINAL_ANSWER":
            print(ev["content"][:800])
    print(SEP)


if __name__ == "__main__":
    asyncio.run(run())
