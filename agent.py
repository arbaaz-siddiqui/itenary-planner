"""agent — LangGraph ReAct agent.

Includes:
- `build_react_agent()` — factory for the LangGraph agent
- Checkpoint store helpers (in-memory + SQLite)
- Response extractors (assistant text, tool calls, options)
- Structured logging setup
- Per-turn Excel benchmark logger
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
from filelock import FileLock
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

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
    base = _load_prompt(f"system_prompt_{SYSTEM_PROMPT_VERSION}.md")
    today = datetime.now().strftime("%A, %d %B %Y")
    parts = [
        base.rstrip(),
        "",
        "## Current context",
        f"- Today's date: {today}",
        f"- Surface: {surface}",
    ]
    if surface == "whatsapp":
        parts.extend(["", _load_prompt("whatsapp_addendum.md").rstrip()])
    return "\n".join(parts)


# =============================================================================
# Agent factory
# =============================================================================
def build_react_agent(
    *,
    surface: str = "streamlit",
    checkpoint_store: BaseCheckpointSaver | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> Any:
    llm = build_llm(temperature=temperature, max_tokens=max_tokens)
    checkpointer = checkpoint_store or build_in_memory_checkpoint()
    return create_react_agent(
        model=llm,
        tools=ALL_TOOLS,
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


def invoke_and_log(
    agent: Any,
    *,
    surface: str,
    thread_id: str,
    user_message: str,
    turn_number: int = 0,
) -> dict[str, Any]:
    config = {"configurable": {"thread_id": thread_id}}
    start = time.perf_counter()
    response = agent.invoke({"messages": [{"role": "user", "content": user_message}]}, config)
    latency = time.perf_counter() - start
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
