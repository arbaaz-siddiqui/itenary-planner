"""agent — LangGraph ReAct agent.

Includes:
- `build_react_agent()` — factory for the LangGraph agent
- Checkpoint store helpers (in-memory + SQLite)
- Response extractors (assistant text, tool calls, options)
- Structured logging setup
- Per-turn Excel benchmark logger
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
from filelock import FileLock
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt import ToolNode

from agent_tools import ALL_TOOLS
from llm import build_llm, get_active_model_id
from settings import get_state_settings


# =============================================================================
# Logging
# =============================================================================
def configure_logging(*, prod: bool = False, level: str = "INFO") -> None:
    """One-time logging setup. Call at app startup."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if prod:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "itinerary_planner") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


# =============================================================================
# Checkpoint stores
# =============================================================================
def build_in_memory_checkpoint() -> BaseCheckpointSaver:
    return MemorySaver()


def build_sqlite_checkpoint(db_path: str | None = None) -> BaseCheckpointSaver:
    path = db_path or get_state_settings().whatsapp_db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(conn=conn)


# =============================================================================
# Prompt loader
# =============================================================================
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT_VERSION = "v1"


@lru_cache(maxsize=4)
def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def load_system_prompt(*, surface: str = "streamlit") -> str:
    from datetime import timezone, timedelta
    _ist = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    today = _ist.strftime("%A, %d %B %Y")
    _year = _ist.year
    # Explicit, emphatic date rule — small models (e.g. llama-3.1-8b) otherwise
    # default bare dates like "3 aug" to a training-era year (2023), producing a
    # PAST date → the flight/hotel API returns 0 results ("no flights available").
    _date_rule = (
        f"- CURRENT YEAR IS {_year}. When the user gives a date without a year "
        f'(e.g. "3 aug", "next Friday"), ALWAYS resolve it to {_year} (or the next '
        f"occurrence if that date already passed this year). NEVER use a past year. "
        f"All search dates you pass to tools must be in {_year} or later, ISO yyyy-mm-dd."
    )

    if surface == "voice":
        # Voice = base prompt + voice addendum (system_prompt_voice.md).
        # The addendum hard-overrides formatting, sentence limits, and bans.
        parts = [
            _load_prompt(f"system_prompt_{SYSTEM_PROMPT_VERSION}.md").rstrip(),
            "",
            _load_prompt("system_prompt_voice.md").rstrip(),
            "",
            "## Current context",
            f"- Today's date: {today}",
            _date_rule,
            f"- Surface: {surface}",
        ]
        return "\n".join(parts)

    base = _load_prompt(f"system_prompt_{SYSTEM_PROMPT_VERSION}.md")
    parts = [
        base.rstrip(),
        "",
        "## Current context",
        f"- Today's date: {today}",
        _date_rule,
        f"- Surface: {surface}",
    ]
    if surface == "whatsapp":
        parts.extend(["", _load_prompt("whatsapp_addendum.md").rstrip()])
    return "\n".join(parts)


# =============================================================================
# Parallel ToolNode — runs all tool calls in a single LLM response concurrently
# =============================================================================
class ParallelToolNode(ToolNode):
    """Drop-in replacement for LangGraph's ToolNode that executes all tool calls
    from one AI message in parallel using asyncio.gather.

    When the LLM emits multiple tool_calls in one response (e.g. search_flights
    + search_hotels), the default ToolNode runs them sequentially:  10s + 6s = 16s.
    This node runs them concurrently:  max(10s, 6s) = 10s — saves ~6s per turn.
    """

    async def _arun_tool(self, tool_call: dict[str, Any], tools_by_name: dict[str, Any]) -> ToolMessage:
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", {})
        tool_call_id = tool_call.get("id", "")
        t = tools_by_name.get(tool_name)
        if t is None:
            return ToolMessage(
                content=f"Tool '{tool_name}' not found.",
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        try:
            if asyncio.iscoroutinefunction(getattr(t, "ainvoke", None)):
                result = await t.ainvoke(tool_args)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, t.invoke, tool_args)
            content = json.dumps(result, default=str) if not isinstance(result, str) else result
        except Exception as e:  # noqa: BLE001
            content = f"Tool error: {e}"
        return ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)

    async def ainvoke(self, state: Any, config: Any = None, **kwargs: Any) -> Any:
        messages = state.get("messages", []) if isinstance(state, dict) else []
        last_ai = next(
            (m for m in reversed(messages) if _message_role(m) in {"ai", "assistant"}),
            None,
        )
        if last_ai is None:
            return await super().ainvoke(state, config, **kwargs)

        tool_calls = _safe_attr(last_ai, "tool_calls") or []
        if len(tool_calls) <= 1:
            return await super().ainvoke(state, config, **kwargs)

        tools_by_name: dict[str, Any] = self.tools_by_name  # type: ignore[attr-defined]

        tool_messages = await asyncio.gather(
            *[self._arun_tool(tc, tools_by_name) for tc in tool_calls]
        )
        return {"messages": list(tool_messages)}

    def invoke(self, state: Any, config: Any = None, **kwargs: Any) -> Any:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(asyncio.run, self.ainvoke(state, config, **kwargs))
                    return fut.result()
            return loop.run_until_complete(self.ainvoke(state, config, **kwargs))
        except Exception:  # noqa: BLE001
            return super().invoke(state, config, **kwargs)


# =============================================================================
# Agent factory
# =============================================================================
def build_react_agent(
    *,
    surface: str = "streamlit",
    checkpoint_store: BaseCheckpointSaver | None = None,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    model_override: str | None = None,
) -> Any:
    # Reply LENGTH is the #1 latency driver: the tool returns in ~5s but the model
    # spends 30-50s WRITING a 250-word wall of text. Cap output hard so replies
    # stay crisp AND fast. Voice is tightest; chat/whatsapp get a bit more room for
    # the option cards but still far below the old 4096 ceiling.
    if max_tokens is None:
        # Voice stays tight (spoken) — short replies are what keep a call fast.
        # Chat/whatsapp show 3 options with the FULL detail set each (baggage,
        # board, cancellation terms) plus a large generate_itinerary_pdf_tool
        # payload; 1500 left no headroom for both, so a rich itinerary could be
        # truncated mid-tool-call. Depth is capped by the 3-option rule in the
        # prompt, not by cutting the model off mid-sentence.
        max_tokens = 300 if surface == "voice" else 2600
    llm = build_llm(temperature=temperature, max_tokens=max_tokens, model_override=model_override)
    checkpointer = checkpoint_store or build_in_memory_checkpoint()

    # Loud warning for the most common mis-config: a BOOKING_TOKEN that lacks the
    # services the app needs (e.g. an Activities-only token silently returns null
    # for hotel availability). Doesn't block startup — just surfaces it.
    from settings import get_booking_api_settings

    missing = get_booking_api_settings().main_token_missing_services()
    if missing:
        get_logger().warning(
            "booking_token_scope_warning",
            missing_services=missing,
            hint="BOOKING_TOKEN is missing serviceTypes; hotel/flight/etc. calls "
            "may return empty. Use the all-services agent token (e.g. GT-021).",
        )

    parallel_tools = ParallelToolNode(ALL_TOOLS)

    return create_react_agent(
        model=llm,
        tools=parallel_tools,
        prompt=load_system_prompt(surface=surface),
        checkpointer=checkpointer,
    )


# =============================================================================
# Response extractors
# =============================================================================
def extract_assistant_text(response: dict[str, Any]) -> str:
    messages = response.get("messages") or []
    for msg in reversed(messages):
        if _message_role(msg) in {"ai", "assistant"}:
            content = _message_content(msg)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(str(c.get("text") or ""))
                    elif isinstance(c, str):
                        parts.append(c)
                if parts:
                    return "\n".join(parts).strip()
    return ""


def extract_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    messages = response.get("messages") or []
    pending: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = _message_role(msg)
        if role == "tool":
            tool_call_id = _safe_attr(msg, "tool_call_id")
            tool_name = _safe_attr(msg, "name") or "<unknown>"
            tool_output = _message_content(msg)
            entry = pending.pop(tool_call_id or "", None) or {}
            out.append(
                {
                    "tool_name": entry.get("tool_name") or tool_name,
                    "input": entry.get("input") or {},
                    "output": tool_output,
                }
            )
        else:
            for tc in _safe_attr(msg, "tool_calls") or []:
                if isinstance(tc, dict):
                    pending[tc.get("id") or ""] = {
                        "tool_name": tc.get("name") or "",
                        "input": tc.get("args") or {},
                    }
    return out


def extract_search_options(response: dict[str, Any]) -> dict[str, Any]:
    for tc in reversed(extract_tool_calls(response)):
        parsed = _coerce_to_dict(tc.get("output"))
        if isinstance(parsed, dict) and isinstance(parsed.get("options"), list):
            return {
                "kind": _kind_from_tool_name(tc.get("tool_name") or ""),
                "options": parsed["options"],
                "raw": parsed,
            }
    return {"kind": None, "options": [], "raw": None}


def _kind_from_tool_name(name: str) -> str:
    name = name.lower()
    for k in ("flight", "hotel", "tour", "transfer", "restaurant", "visa", "package"):
        if k in name:
            return k
    return "unknown"


def _message_role(msg: Any) -> str:
    type_attr = _safe_attr(msg, "type")
    if type_attr:
        return str(type_attr)
    if isinstance(msg, dict):
        return str(msg.get("role") or msg.get("type") or "")
    return ""


def _message_content(msg: Any) -> Any:
    if hasattr(msg, "content"):
        return msg.content
    if isinstance(msg, dict):
        return msg.get("content")
    return None


def _safe_attr(msg: Any, name: str) -> Any:
    if hasattr(msg, name):
        return getattr(msg, name)
    if isinstance(msg, dict):
        return msg.get(name)
    return None


def _coerce_to_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            return None
    return None


# =============================================================================
# Excel benchmark logger
# =============================================================================
BENCHMARK_FILE = Path("benchmark_results.xlsx")
LOCK_FILE = Path("benchmark_results.xlsx.lock")

BENCHMARK_COLUMNS: list[str] = [
    "timestamp",
    "model",
    "surface",
    "thread_id",
    "turn_number",
    "user_message",
    "assistant_text",
    "tools_called",
    "tool_call_details",
    "tool_results_summary",
    "latency_seconds",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "grade_tool_accuracy",
    "grade_no_fabrication",
    "grade_multi_turn",
    "grade_style",
    "grade_overall",
]

PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.8, 4.0),
    "mistralai/mistral-large-2411": (2.0, 6.0),
    "qwen/qwen-2.5-72b-instruct": (0.35, 0.40),
    "meta-llama/llama-3.3-70b-instruct": (0.10, 0.32),
    "deepseek/deepseek-chat-v3-0324": (0.27, 1.10),
}


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    per_in, per_out = PRICING_PER_MILLION.get(model, (0.0, 0.0))
    return round((input_tokens * per_in + output_tokens * per_out) / 1_000_000, 6)


def _extract_tokens(response: dict[str, Any]) -> tuple[int, int]:
    messages = response.get("messages") or []
    for msg in reversed(messages):
        meta = getattr(msg, "usage_metadata", None) or (
            msg.get("usage_metadata") if isinstance(msg, dict) else None
        )
        if isinstance(meta, dict):
            return (
                int(meta.get("input_tokens") or 0),
                int(meta.get("output_tokens") or 0),
            )
    return (0, 0)


def _summarize_result(output: Any) -> str:
    if isinstance(output, dict):
        if output.get("error"):
            return f"error:{output.get('error_type', 'unknown')}"
        opts = output.get("options")
        if isinstance(opts, list):
            return f"options:{len(opts)}"
        return "ok"
    if isinstance(output, str):
        try:
            return _summarize_result(json.loads(output))
        except ValueError:
            return "string"
    return "unknown"


def log_turn(
    *,
    model: str,
    surface: str,
    thread_id: str,
    user_message: str,
    agent_response: dict[str, Any],
    latency_seconds: float,
    turn_number: int = 0,
) -> None:
    """Append one row to benchmark_results.xlsx. Best-effort."""
    log = get_logger("agent.benchmark")
    try:
        import pandas as pd

        assistant_text = extract_assistant_text(agent_response)
        tool_calls = extract_tool_calls(agent_response)
        input_tokens, output_tokens = _extract_tokens(agent_response)

        row = {
            "timestamp": datetime.now(UTC).isoformat(),
            "model": model,
            "surface": surface,
            "thread_id": thread_id,
            "turn_number": turn_number,
            "user_message": user_message,
            "assistant_text": assistant_text,
            "tools_called": ",".join(tc.get("tool_name", "") for tc in tool_calls),
            "tool_call_details": json.dumps(
                [{"name": tc.get("tool_name"), "input": tc.get("input")} for tc in tool_calls],
                default=str,
            ),
            "tool_results_summary": json.dumps(
                [
                    {
                        "name": tc.get("tool_name"),
                        "result_kind": _summarize_result(tc.get("output")),
                    }
                    for tc in tool_calls
                ],
                default=str,
            ),
            "latency_seconds": round(latency_seconds, 3),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": _compute_cost(model, input_tokens, output_tokens),
            "grade_tool_accuracy": "",
            "grade_no_fabrication": "",
            "grade_multi_turn": "",
            "grade_style": "",
            "grade_overall": "",
        }

        with FileLock(str(LOCK_FILE), timeout=10):
            if BENCHMARK_FILE.exists():
                existing = pd.read_excel(BENCHMARK_FILE)
                new_df = pd.concat(
                    [existing, pd.DataFrame([row], columns=BENCHMARK_COLUMNS)],
                    ignore_index=True,
                )
            else:
                new_df = pd.DataFrame([row], columns=BENCHMARK_COLUMNS)
            new_df.to_excel(BENCHMARK_FILE, index=False)
    except Exception as e:
        log.warning("benchmark_log_failed", error=str(e), error_type=type(e).__name__)


import os as _os
import sys as _sys

_TL_COLOR = _sys.stderr.isatty() and _os.environ.get("NO_COLOR") is None


def _tc(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TL_COLOR else text


def print_turn_timeline(
    *, surface: str, user_message: str, agent_response: dict[str, Any],
    latency_seconds: float,
) -> None:
    """Compact, colored per-turn timeline: what the user said, which tools fired
    + whether each returned data, total time, reply length. Prints to stderr so
    it sits alongside the [BOOKING-API] lines but reads at a glance."""
    try:
        tool_calls = extract_tool_calls(agent_response)
        reply = extract_assistant_text(agent_response) or ""
        served = _serving_model(agent_response)   # ACTUAL model OpenRouter used
        secs = latency_seconds
        secs_txt = _tc(f"{secs:.1f}s", "33" if secs > 15 else "32")  # slow = yellow
        head = _tc("┌─ TURN", "36;1")
        model_txt = _tc(f"model={served}", "34") if served else ""
        print(f"\n{head}  [{surface}]  {secs_txt}  ·  reply {len(reply.split())}w  ·  {model_txt}", file=_sys.stderr)
        print(f"{_tc('│', '36')} 🧑 {user_message[:90]}", file=_sys.stderr)
        if not tool_calls:
            print(f"{_tc('│', '36')} {_tc('⚠ no tools called', '33')} (answered from context / asked a question)", file=_sys.stderr)
        for tc in tool_calls:
            name = tc.get("tool_name", "?")
            out = tc.get("output")
            kind = _summarize_result(out)
            got = "✓" if kind not in ("empty", "error", "none", "") and kind else "✗"
            got_c = _tc(got, "32" if got == "✓" else "31")
            # Did this result come from the cache? (from_cache flag in the output)
            cache_tag = ""
            if _is_from_cache(out):
                cache_tag = " " + _tc("[CACHED]", "33;1")
            inp = tc.get("input") or {}
            inp_s = ", ".join(f"{k}={v}" for k, v in list(inp.items())[:3])
            print(f"{_tc('│', '36')} 🔧 {_tc(name, '35')}({inp_s[:60]}) {got_c} {kind}{cache_tag}", file=_sys.stderr)
        print(f"{_tc('└─', '36')} 🤖 {reply[:110].replace(chr(10),' ')}", file=_sys.stderr)
    except Exception:  # never let logging break a turn
        pass


def _serving_model(agent_response: dict[str, Any]) -> str | None:
    """The model OpenRouter ACTUALLY used this turn (from response_metadata) —
    ground truth for confirming a UI model-switch really took effect."""
    try:
        for m in reversed(agent_response.get("messages", [])):
            md = getattr(m, "response_metadata", None) or {}
            name = md.get("model_name") or md.get("model")
            if name:
                return str(name)
    except Exception:  # noqa: BLE001
        pass
    return None


def _is_from_cache(output: Any) -> bool:
    """True if a tool result was served from the result cache (from_cache flag)."""
    import json as _json
    if isinstance(output, dict):
        return bool(output.get("from_cache"))
    if isinstance(output, str):
        try:
            return bool(_json.loads(output).get("from_cache"))
        except Exception:  # noqa: BLE001
            return '"from_cache": true' in output.lower()
    return False


def invoke_and_log(
    agent: Any,
    *,
    surface: str,
    thread_id: str,
    user_message: str,
    turn_number: int = 0,
) -> dict[str, Any]:
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    # NOTE: we deliberately do NOT set a tight recursion_limit for voice. A low
    # limit cuts the agent off MID-tool-call (AIMessage with tool_calls but no
    # ToolMessage), which corrupts the checkpointed thread and makes EVERY later
    # turn fail with INVALID_CHAT_HISTORY ("I hit a snag"). Latency on voice is
    # instead controlled by the prompt (one search per turn) + the SSE filler
    # line. Keep a generous cap only as a runaway backstop.
    if surface == "voice":
        config["recursion_limit"] = 25
    start = time.perf_counter()
    response = agent.invoke({"messages": [{"role": "user", "content": user_message}]}, config)
    latency = time.perf_counter() - start
    # Colored per-turn timeline to the console: serving model, tools, [CACHED] tags.
    print_turn_timeline(
        surface=surface, user_message=user_message,
        agent_response=response, latency_seconds=latency,
    )
    log_turn(
        model=get_active_model_id(),
        surface=surface,
        thread_id=thread_id,
        user_message=user_message,
        agent_response=response,
        latency_seconds=latency,
        turn_number=turn_number,
    )
    return response


# =============================================================================
# Streaming
# =============================================================================
class StreamResult:
    """Mutable holder populated as a turn streams.

    `stream_and_log` yields assistant-text tokens (for `st.write_stream`) while
    filling this in. After the stream is exhausted, `.response` is shaped like
    `invoke()`'s output (a `{"messages": [...]}` dict) so the existing
    extractors (`extract_tool_calls`, `extract_search_options`, …) work
    unchanged, and `.tool_event_log` carries a live trace of which tools fired
    and in what order — surfaced in the debug UI.
    """

    def __init__(self) -> None:
        self.text: str = ""
        self.messages: list[Any] = []
        self.tool_event_log: list[dict[str, Any]] = []
        self.latency_seconds: float = 0.0

    @property
    def response(self) -> dict[str, Any]:
        return {"messages": self.messages}


def stream_and_log(
    agent: Any,
    *,
    surface: str,
    thread_id: str,
    user_message: str,
    turn_number: int = 0,
    result: StreamResult | None = None,
):
    """Stream a turn token-by-token, yielding assistant text as it is produced.

    Uses LangGraph's `messages` + `updates` stream modes:
      - `messages` → (token_chunk, metadata) for live assistant text. We only
        forward tokens from the agent's final answer node, never tool-internal
        LLM chatter.
      - `updates`  → per-node state deltas; we harvest the full AI/Tool messages
        from these so the final `result.response` matches `invoke()` output and
        tool calls/options can be extracted exactly as before.

    Yields:
        str tokens — feed straight into `st.write_stream`.

    Side effects:
        Populates `result` (a StreamResult) and logs the turn on completion.
    """
    holder = result if result is not None else StreamResult()
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if surface == "voice":
        config["recursion_limit"] = 25
    else:
        # Backstop against tool-loops (some models re-call the same search 5-7×,
        # hanging the turn for 90s). ~12 steps = the model + up to ~5 tool cycles,
        # plenty for a legit multi-tool turn, but caps a runaway loop. Generous
        # enough not to cut mid-tool-call (which would corrupt the thread).
        config["recursion_limit"] = 12
    start = time.perf_counter()

    def _process_chunk(mode: str, chunk: Any) -> list[str]:
        """Process one stream chunk, return any text tokens to yield."""
        tokens: list[str] = []
        if mode == "messages":
            msg_chunk, metadata = chunk
            node = (metadata or {}).get("langgraph_node")
            if node and node != "agent":
                return tokens
            if _safe_attr(msg_chunk, "tool_calls") or _safe_attr(msg_chunk, "tool_call_chunks"):
                return tokens
            token = _token_text(msg_chunk)
            if token and not _looks_like_tool_scaffolding(token):
                holder.text += token
                tokens.append(token)
        elif mode == "updates":
            for node_name, state in (chunk or {}).items():
                msgs = (state or {}).get("messages") if isinstance(state, dict) else None
                for m in msgs or []:
                    holder.messages.append(m)
                    _record_tool_events(m, node_name, holder)
        return tokens

    def _clear_thread() -> None:
        try:
            cp = agent.checkpointer
            if hasattr(cp, "storage"):
                cp.storage.pop(thread_id, None)  # type: ignore[attr-defined]
        except Exception:
            pass

    # First attempt
    _attempted_recovery = False
    try:
        for mode, chunk in agent.stream(
            {"messages": [{"role": "user", "content": user_message}]},
            config,
            stream_mode=["messages", "updates"],
        ):
            yield from _process_chunk(mode, chunk)
    except ValueError as _ve:
        if "tool_calls" in str(_ve) and "ToolMessage" in str(_ve):
            # Corrupted checkpoint — clear thread and retry once from scratch
            _clear_thread()
            holder.text = ""
            holder.messages = []
            for mode, chunk in agent.stream(
                {"messages": [{"role": "user", "content": user_message}]},
                config,
                stream_mode=["messages", "updates"],
            ):
                yield from _process_chunk(mode, chunk)
        else:
            raise

    holder.latency_seconds = time.perf_counter() - start
    log_turn(
        model=get_active_model_id(),
        surface=surface,
        thread_id=thread_id,
        user_message=user_message,
        agent_response=holder.response,
        latency_seconds=holder.latency_seconds,
        turn_number=turn_number,
    )


# Tool-call scaffolding some models leak as plain text, e.g.
# `search_hotels{"destination_city": ...}`, `// Retry with broader search`,
# `enumerate_package_details{}`. A token matching this is internal, not a reply.
_TOOL_NAMES_RE = re.compile(
    r"\b(search_flights|search_hotels|search_tours|search_restaurants|get_visa_info|"
    r"list_packages|get_exchange_rate|resolve_party_tool|check_floor_tool|"
    r"search_airport_transfer_dubai|enumerate_package_details|generate_itinerary_pdf"
    r"|[a-z_]+_tool)\s*\{",
    re.IGNORECASE,
)


def _looks_like_tool_scaffolding(token: str) -> bool:
    """True if a streamed token is leaked tool-call syntax, not a real reply."""
    t = token.strip()
    if not t:
        return False
    return bool(_TOOL_NAMES_RE.search(t)) or t.startswith("//")


def _token_text(msg_chunk: Any) -> str:
    """Extract printable text from a streamed message chunk (str or block list)."""
    content = _message_content(msg_chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text") or ""))
            elif isinstance(c, str):
                parts.append(c)
        return "".join(parts)
    return ""


def _record_tool_events(msg: Any, node_name: str, holder: StreamResult) -> None:
    """Note tool-call requests and tool results as they stream in, in order."""
    for tc in _safe_attr(msg, "tool_calls") or []:
        if isinstance(tc, dict):
            holder.tool_event_log.append(
                {"event": "call", "tool_name": tc.get("name") or "", "node": node_name}
            )
    if _message_role(msg) == "tool":
        holder.tool_event_log.append(
            {"event": "result", "tool_name": _safe_attr(msg, "name") or "", "node": node_name}
        )
