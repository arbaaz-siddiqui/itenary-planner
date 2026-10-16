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
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
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

from agent_tools import ALL_TOOLS, tools_for_surface
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


def build_postgres_checkpoint(conn_string: str | None = None) -> BaseCheckpointSaver:
    """LangGraph checkpointer backed by Postgres, for the Next.js surface.

    Streamlit uses the in-memory store, which is why a browser refresh loses
    the conversation. This makes agent state outlive the process, so a customer
    can close the tab and come back.

    Deliberately its own connection pool, not the one api/db.py uses for the
    transcript: a burst of checkpoint writes must not exhaust the connections
    the UI needs to render a page.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    if conn_string is None:
        from api.db import database_url

        conn_string = database_url()

    pool = ConnectionPool(
        conn_string,
        min_size=1,
        max_size=5,
        # autocommit is required: PostgresSaver issues its own transactions, and
        # psycopg's default implicit transaction leaves them open, so writes are
        # never visible to the next turn.
        kwargs={"autocommit": True, "row_factory": None},
        open=True,
    )
    saver = PostgresSaver(pool)
    # Creates LangGraph's own tables on first use. Idempotent, and separate
    # from migrations/001_init.sql because these tables are LangGraph's
    # implementation detail — ours must not depend on their shape.
    saver.setup()
    return saver


def build_sqlite_checkpoint(db_path: str | None = None) -> BaseCheckpointSaver:
    path = db_path or get_state_settings().whatsapp_db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(conn=conn)


# =============================================================================
# Prompt loader
# =============================================================================
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
# Which prompts/system_prompt_<version>.md to load. v3 is the default:
# same rules as v2 at ~1.8k tokens (v2 ~7.1k). SYSTEM_PROMPT_VERSION=v2
# falls back without a redeploy.
SYSTEM_PROMPT_VERSION = os.environ.get("SYSTEM_PROMPT_VERSION", "v3").strip() or "v3"


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
            # The model sometimes emits a whole call as the NAME, e.g.
            # `get_tour_options(tour_id:28488,...)<tool_call|>`. Recover the
            # real tool rather than burning a round-trip on "not found".
            base = re.split(r"[(\{<\s]", tool_name, 1)[0].strip()
            t = tools_by_name.get(base)
            if t is None:
                return ToolMessage(
                    content=(
                        f"Tool '{tool_name}' not found. Call it as a proper tool "
                        "with JSON arguments, not as text."
                    ),
                    tool_call_id=tool_call_id,
                    name=tool_name,
                )
            logger_ = logging.getLogger("agent.turn")
            logger_.warning("recovered malformed tool name %r -> %r", tool_name, base)
            tool_name = base
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

    # Surface-scoped: voice has no screen and cannot receive a PDF, WhatsApp has
    # no image cards. Handing a surface tools it cannot use costs schema tokens
    # on every request and gives the model more ways to route wrong.
    parallel_tools = ParallelToolNode(tools_for_surface(surface))

    return create_react_agent(
        model=llm,
        tools=parallel_tools,
        prompt=load_system_prompt(surface=surface),
        pre_model_hook=_supersede_earlier_searches,
        post_model_hook=_split_concatenated_args,
        checkpointer=checkpointer,
    )


#: Searches whose earlier results the model must not reuse once the customer
#: has spoken again. Restaurant answers depend on the arguments (area, budget,
#: cuisine, dish) and the model re-filtered old rows instead of searching: asked
#: for "dinner in Deira" after a Marina search, it re-listed the Marina rows as
#: "nearby", and asked for a ₹500 budget it quoted starting prices as meals.
_SUPERSEDED_EACH_TURN = frozenset({"search_restaurants"})


def _supersede_earlier_searches(state: dict[str, Any]) -> dict[str, Any]:
    """Show the model a stub, not the rows, of searches from earlier turns.

    Only the model's INPUT changes (`llm_input_messages`); the stored thread
    keeps every result, so cards, transcripts and logs are untouched. Results
    from the current turn -- everything after the latest customer message --
    stay whole, so the search the model makes now is read in full.
    """
    from langchain_core.messages import ToolMessage

    msgs = list(state.get("messages") or [])
    last_human = max(
        (i for i, m in enumerate(msgs) if _message_role(m) in {"human", "user"}), default=-1
    )
    out: list[Any] = []
    for i, m in enumerate(msgs):
        name = str(_safe_attr(m, "name") or "")
        if i < last_human and _message_role(m) == "tool" and name in _SUPERSEDED_EACH_TURN:
            asked = (_coerce_to_dict(_message_content(m)) or {}).get("search_params") or {}
            m = ToolMessage(
                content=(
                    f"[superseded] Earlier {name} result (asked: "
                    f"{json.dumps(asked, ensure_ascii=False, default=str)}). Its rows "
                    f"are not shown again: for the customer's current message call "
                    f"{name} again with what they asked for now, and answer from that. "
                    f"A question about ONE restaurant's cuisine, menu, prices, rating or "
                    f"hours → get_restaurant_details(restaurant_id) — never answer it "
                    f"from an earlier reply."
                ),
                tool_call_id=_safe_attr(m, "tool_call_id") or "",
                name=name,
            )
        out.append(m)
    return {"llm_input_messages": out}


_INLINE_CALL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)?\s*(?:<\|?\s*tool[ _]call\s*\|?>)?\s*$", re.S)
_TEMPLATE_JUNK = re.compile(r"<\|?\s*\"?\s*\|?>|<\|[^>]*\|>")


def _coerce_scalar(value: str) -> Any:
    v = value.strip().strip("\"'").strip()
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def repair_inline_tool_call(call: dict[str, Any]) -> dict[str, Any] | None:
    """A call whose arguments were written into its NAME, made runnable.

    Some models emit `get_tour_details(tour id:53632)<tool call|>` as the tool
    name with empty arguments. No tool has that name, so the call failed, the
    step line showed the raw text, and the agent told the customer it had a
    technical problem. The name is the real tool; the parentheses hold
    `key:value` (or `key=value`) pairs. Returns None when nothing needs fixing.
    """
    name = str(call.get("name") or "")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return None
    m = _INLINE_CALL.match(_TEMPLATE_JUNK.sub("", name))
    if not m:
        return None
    base, inner = m.group(1), m.group(2) or ""
    args = dict(call.get("args") or {}) if isinstance(call.get("args"), dict) else {}
    for part in re.split(r",(?=\s*[A-Za-z_][A-Za-z0-9_ ]*\s*[:=])", inner):
        if not re.search(r"[:=]", part):
            continue
        key, _, value = re.split(r"([:=])", part, maxsplit=1)
        key = re.sub(r"\s+", "_", key.strip())
        if key and key not in args:
            args[key] = _coerce_scalar(value)
    return {**call, "name": base, "args": args}


def _split_concatenated_args(state: dict[str, Any]) -> dict[str, Any] | None:
    """Split a tool call whose arguments are two JSON objects stuck together.

    Asked to compare two restaurants the model wants two `lookup_entity` calls
    and emits ONE whose arguments are `{...}{...}`. LangChain accepts that, but
    the provider rejects the message when it is replayed in history, returning
    HTTP 200 with `{"error": "Extra data: line 1 column 64"}` — so the turn dies
    and the customer gets a blank reply, permanently, because the poisoned
    message is now in the thread.

    The objects are cleanly separable with raw_decode, so recover the calls the
    model intended rather than losing the turn.
    """
    msgs = (state or {}).get("messages") or []
    if not msgs:
        return None
    last = msgs[-1]
    if last.__class__.__name__ != "AIMessage":
        return None

    raw_calls = list(getattr(last, "tool_calls", None) or [])
    invalid = list(getattr(last, "invalid_tool_calls", None) or [])
    if not raw_calls and not invalid:
        return None

    decoder = json.JSONDecoder()
    fixed: list[dict[str, Any]] = []
    changed = False
    calls: list[dict[str, Any]] = []
    for call in [*raw_calls, *invalid]:
        repaired_call = repair_inline_tool_call(call)
        if repaired_call is not None:
            changed = True
            logging.getLogger("agent.turn").warning(
                "repaired inline tool call %r -> %s(%s)", call.get("name"),
                repaired_call["name"], repaired_call["args"],
            )
            call = repaired_call
        calls.append(call)
    for call in calls:
        args = call.get("args")
        # A recoverable call carries its arguments as an unparsed string.
        text = args if isinstance(args, str) else None
        if text is None:
            fixed.append(call)
            continue
        parts: list[Any] = []
        idx = 0
        try:
            while idx < len(text):
                obj, end = decoder.raw_decode(text, idx)
                parts.append(obj)
                idx = end
                # Skip whitespace or a stray comma between the two objects.
                while idx < len(text) and (text[idx].isspace() or text[idx] == ","):
                    idx += 1
        except ValueError:
            parts = []
        if len(parts) < 2:
            fixed.append(call)
            continue
        changed = True
        for i, obj in enumerate(parts):
            fixed.append({
                "name": call.get("name", ""),
                "args": obj,
                "id": f"{call.get('id') or 'call'}_{i}",
                "type": "tool_call",
            })

    dropped = _drop_repeated_failures(msgs, fixed)
    if dropped is not None:
        fixed = dropped
        changed = True

    if not changed:
        return None
    logging.getLogger("agent.turn").warning(
        "split concatenated tool args into %d calls", len(fixed)
    )
    repaired = last.model_copy(update={"tool_calls": fixed, "invalid_tool_calls": []})
    repaired.id = last.id
    return {"messages": [repaired]}


_PDF_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://)?[^)\s]*/itinerary/[^)\s]+\)|(?:https?://)?\S*/itinerary/\S+\.pdf")


def _strip_pdf_links(response: dict[str, Any]) -> None:
    """Remove any itinerary-PDF URL the model wrote into its reply.

    The tool stopped returning a URL, but the link also travels in the thread:
    once one reply carries it, later turns copy it forward verbatim. Those
    links 404 as soon as the file moves -- a container restart, or a base URL
    pointing at a host that never held the file -- and the customer sees a dead
    link where the download button belongs. A markdown link keeps its text so
    the sentence still reads.
    """
    messages = response.get("messages") or []
    for msg in reversed(messages):
        if _message_role(msg) not in {"ai", "assistant"}:
            continue
        text = _message_content(msg)
        if not isinstance(text, str) or "/itinerary/" not in text:
            return
        cleaned = _PDF_LINK.sub(lambda m: m.group(1) or "", text)
        cleaned = re.sub(r"[ 	]{2,}", " ", cleaned).strip()
        if hasattr(msg, "content"):
            msg.content = cleaned
        elif isinstance(msg, dict):
            msg["content"] = cleaned
        return


_PDF_CLAIM = re.compile(
    r"(?:ready to download|download (?:it|the|your)[^.\n]*|\[[^\]]*download[^\]]*\])",
    re.IGNORECASE,
)


def _pdf_id_in_thread(msgs: list[Any]) -> str:
    """The itinerary_id of a PDF this thread actually produced, or ""."""
    for msg in reversed(msgs):
        if _message_role(msg) != "tool":
            continue
        if "pdf" not in str(_safe_attr(msg, "name") or ""):
            continue
        out = _coerce_to_dict(_message_content(msg)) or {}
        if out.get("itinerary_id"):
            return str(out["itinerary_id"])
    return ""


def _correct_unbuilt_pdf_claim(response: dict[str, Any]) -> None:
    """Stop a reply promising a download when no PDF was built.

    generate_itinerary_pdf_tool refuses a trip with no day_plans (a PDF with a
    price table and no schedule is worse than none). The model answered that
    refusal with "the itinerary is ready to download below" and even typed
    "[Download Itinerary PDF]" as literal text -- so the customer read success,
    saw something button-shaped, and clicked nothing. Asked again, it made no
    tool call at all and repeated the claim.

    A promise of a download is only honest when a tool actually returned an
    itinerary_id, because that id is what renders the real button.
    """
    messages = response.get("messages") or []
    if _pdf_id_in_thread(messages):
        return
    for msg in reversed(messages):
        if _message_role(msg) not in {"ai", "assistant"}:
            continue
        text = _message_content(msg)
        if not isinstance(text, str) or not _PDF_CLAIM.search(text):
            return
        replacement = (
            "I could not build the PDF yet — it needs a day-by-day plan, and we "
            "have not settled one. Tell me which tours you want (or say \"you "
            "pick\") and I will put the schedule together and generate it."
        )
        if hasattr(msg, "content"):
            msg.content = replacement
        elif isinstance(msg, dict):
            msg["content"] = replacement
        return


def _replace_invented_results(response: dict[str, Any]) -> None:
    """Replace a reply that lists results no search produced.

    A search tool that is missing a mandatory field returns a refusal naming
    what to ask for, and no results. The model sometimes answers it with a
    table anyway: asked for flights with the origin city missing, it printed
    four airlines with departure times and fares, none of which came from the
    supplier. The customer cannot tell that apart from a real quote.

    So when the only search results this turn were refusals and the reply still
    contains a priced table, the reply is discarded and the tool's own question
    is asked instead. Fabricated prices are worse than an extra turn.
    """
    msgs = response.get("messages") or []
    refusals: list[dict[str, Any]] = []
    produced_results = False

    for msg in msgs:
        if msg.__class__.__name__ != "ToolMessage":
            continue
        parsed = _coerce_to_dict(getattr(msg, "content", None))
        if not isinstance(parsed, dict):
            continue
        if parsed.get("error_type") == "NeedsCustomerInput":
            refusals.append(parsed)
        elif parsed.get("options"):
            produced_results = True

    if produced_results or not refusals:
        return

    last = next(
        (m for m in reversed(msgs) if _message_role(m) in {"ai", "assistant"}), None
    )
    text = _message_content(last) if last is not None else None
    if not isinstance(text, str) or "₹" not in text:
        return

    # Worded for the customer. rules._FIELD_PROMPTS is written for the model
    # ("which city they are flying from") and reads wrong addressed to someone.
    ASK_FOR = {
        "origin_city": "which city you are flying from",
        "departure_date": "your departure date",
        "check_in": "your check-in date",
        "check_out": "your check-out date",
        "travel_date": "your travel date",
        "adults": "how many adults are travelling",
        "child_ages": "the age of each child",
    }

    fields: list[str] = []
    for r in refusals:
        for f in r.get("missing_fields") or []:
            if f not in fields:
                fields.append(f)
    asks = [ASK_FOR.get(f, f.replace("_", " ")) for f in fields]
    joined = asks[0] if len(asks) == 1 else ", ".join(asks[:-1]) + f" and {asks[-1]}"

    logging.getLogger("agent.turn").warning(
        "replaced a reply that quoted prices with no search behind it (missing: %s)",
        ", ".join(fields),
    )
    last.content = (
        f"Before I can pull live prices I need {joined}. Could you tell me "
        f"{'that' if len(asks) == 1 else 'those'}?"
    )


_RUPEE_AMOUNT = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)")
_ANY_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

_RESEARCH_MARKER = (
    "[system] Your last reply answered from memory instead of a search. Prices "
    "are live and must never be retyped or pointed back to. Call the search "
    "tool again now with the customer's details (a repeat search is fast) and "
    "answer only from its result, with no comment about searching again."
)
# "I have already pulled those", "see the table above".
_POINTS_BACK = re.compile(r"\b(already|above|earlier|previous(ly)?)\b", re.IGNORECASE)


def _tool_numbers(messages: list[Any]) -> set[int]:
    """Every number a tool returned or the customer typed in this thread, in
    whole rupees. The customer's own figures ("budget Rs 50,000") may be echoed."""
    seen: set[int] = set()
    for msg in messages:
        if _message_role(msg) not in {"tool", "human", "user"}:
            continue
        content = _message_content(msg)
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        for raw in _ANY_NUMBER.findall(text or ""):
            try:
                seen.add(round(float(raw.replace(",", ""))))
            except ValueError:
                continue
    return seen


def _repeats_earlier_request(history: list[Any]) -> bool:
    """True when the customer's latest message is one they already sent."""
    said = [
        " ".join(_customer_part(str(_message_content(m) or "")).lower().split())
        for m in history
        if _message_role(m) in {"human", "user"}
    ]
    said = [s for s in said if s]
    return len(said) >= 2 and said[-1] in said[:-1]


def _unsourced_prices(history: list[Any], turn: list[Any]) -> list[str]:
    """Rupee amounts in a tool-less reply that no tool result ever contained.

    Asked for the same flights a second time, the model skipped the search,
    said "I have already found some flight options" and retyped a table from
    memory: Flydubai Rs 56,494.41 and IndiGo Rs 61,545.94 (the supplier had
    quoted Rs 36,380.55 and Rs 32,242.87), then called a third figure the
    cheapest. Nothing marks such a table as invented, so a price is accepted
    only when some tool in this conversation returned it.
    """
    if extract_tool_calls({"messages": turn}):
        return []
    text = _final_reply({"messages": turn})
    amounts = _RUPEE_AMOUNT.findall(text or "")
    if (amounts or _POINTS_BACK.search(text or "")) and _repeats_earlier_request(history):
        # Real prices, but the customer asked for the search again and got
        # "I have already pulled them" with no cards. Prices are live: run it.
        return amounts or ["(repeated request)"]
    if not amounts:
        return []
    known = _tool_numbers(history)
    unsourced: list[str] = []
    for raw in amounts:
        try:
            value = round(float(raw.replace(",", "")))
        except ValueError:
            continue
        if not (known & {value - 1, value, value + 1}):
            unsourced.append(raw)
    return unsourced


def _thread_messages(agent: Any, config: dict[str, Any]) -> list[Any]:
    try:
        state = agent.get_state(config)
    except Exception:  # noqa: BLE001 — no checkpoint means no history to check
        return []
    return list((getattr(state, "values", None) or {}).get("messages") or [])


def _failed_calls_so_far(msgs: list[Any]) -> set[tuple[str, str]]:
    """(tool name, arguments) pairs that already returned NeedsCustomerInput.

    Read from the thread rather than tracked separately so it survives the
    replay LangGraph does on every step.
    """
    seen: set[tuple[str, str]] = set()
    by_id: dict[str, tuple[str, str]] = {}
    for msg in msgs:
        for call in getattr(msg, "tool_calls", None) or []:
            cid = call.get("id")
            if cid:
                by_id[cid] = (
                    call.get("name", ""),
                    json.dumps(call.get("args"), sort_keys=True, default=str),
                )
        if msg.__class__.__name__ != "ToolMessage":
            continue
        key = by_id.get(getattr(msg, "tool_call_id", ""))
        if key and "NeedsCustomerInput" in str(getattr(msg, "content", "")):
            seen.add(key)
    return seen


def _drop_repeated_failures(
    msgs: list[Any], calls: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """Remove calls whose exact arguments already earned a refusal.

    A missing mandatory field makes the tool refuse and name what to ask for.
    The model sometimes re-sends the identical arguments instead, and with
    nothing to break the cycle it did so 42 times in one turn -- burning the
    recursion budget and the credit balance, then answering from invention
    because it never got a result.

    Dropping the duplicate leaves the earlier refusal as the only thing in
    front of the model, which is the message that tells it what to ask.
    """
    failed = _failed_calls_so_far(msgs)
    if not failed:
        return None
    kept = [
        c
        for c in calls
        if (c.get("name", ""), json.dumps(c.get("args"), sort_keys=True, default=str))
        not in failed
    ]
    if len(kept) == len(calls):
        return None
    logging.getLogger("agent.turn").warning(
        "dropped %d repeated tool call(s) that already failed",
        len(calls) - len(kept),
    )
    return kept



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
    from mcp_tools.ui_payload import full_result

    for tc in reversed(extract_tool_calls(response)):
        parsed = _coerce_to_dict(tc.get("output"))
        if isinstance(parsed, dict) and isinstance(parsed.get("options"), list):
            # The model saw a slim copy; the cards get the full one.
            parsed = full_result(parsed)
            return {
                "kind": _kind_from_tool_name(tc.get("tool_name") or ""),
                "options": parsed["options"],
                "raw": parsed,
            }
    return {"kind": None, "options": [], "raw": None}


def extract_all_search_options(response: dict[str, Any]) -> list[dict[str, Any]]:
    """One card group per kind of result this turn produced, in order.

    A reply often covers two things at once (hotels and the airport transfer
    to them). Taking only the last search left the hotels without cards. The
    latest search of each kind wins, so a refined search replaces the first.
    """
    from mcp_tools.ui_payload import full_result

    by_kind: dict[str, dict[str, Any]] = {}
    for tc in extract_tool_calls(response):
        parsed = _coerce_to_dict(tc.get("output"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("options"), list):
            continue
        parsed = full_result(parsed)
        if not parsed.get("options"):
            continue
        kind = _kind_from_tool_name(tc.get("tool_name") or "")
        by_kind.pop(kind, None)  # re-insert so order follows the latest search
        by_kind[kind] = {"kind": kind, "options": parsed["options"], "raw": parsed}
    return list(by_kind.values())


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
            existing = None
            # A run killed mid-write leaves a 0-byte file. exists() is then True
            # and read_excel raises "Excel file format cannot be determined" on
            # EVERY later turn, so benchmark logging stays broken until someone
            # deletes it by hand. Treat an unreadable file as absent.
            if BENCHMARK_FILE.exists() and BENCHMARK_FILE.stat().st_size > 0:
                try:
                    existing = pd.read_excel(BENCHMARK_FILE)
                except Exception:  # noqa: BLE001 — corrupt file, start fresh
                    log.warning("benchmark_file_unreadable_recreating")
            if existing is not None:
                new_df = pd.concat(
                    [existing, pd.DataFrame([row], columns=BENCHMARK_COLUMNS)],
                    ignore_index=True,
                )
            else:
                new_df = pd.DataFrame([row], columns=BENCHMARK_COLUMNS)
            # Write to a temp file then replace, so an interrupted write cannot
            # truncate the real one.
            tmp = BENCHMARK_FILE.with_suffix(".xlsx.tmp")
            new_df.to_excel(tmp, index=False)
            tmp.replace(BENCHMARK_FILE)
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


# The provider rejects a turn when it cannot parse the tool call the model
# emitted, and words it differently each time: "arguments must be a JSON
# object", and the json module's own parse errors verbatim ("Extra data: line 1
# column 85", "Expecting value"). Matching one wording let the others escape as
# a raw traceback in front of the customer, so match the family.
_MALFORMED_ARGS_SIGNS = (
    "arguments must be a json object",
    "extra data: line",
    "expecting value: line",
    "expecting ',' delimiter",
    "expecting property name",
    "unterminated string starting at",
)


def _is_malformed_tool_args(exc: Exception) -> bool:
    """True for the provider's 400 on a tool call it could not parse."""
    text = str(exc).lower()
    if any(sign in text for sign in _MALFORMED_ARGS_SIGNS):
        return True
    return "tool_calls" in text and "json object" in text


def _repair_thread(agent: Any, config: dict[str, Any]) -> None:
    """Drop malformed tool-call messages from a poisoned checkpoint.

    A tool_calls entry whose args are not a dict makes the provider reject the
    entire request, forever, because history is replayed each turn. We remove
    those AIMessages and any ToolMessage left orphaned by their removal.
    """
    import logging

    logger = logging.getLogger("agent.turn")
    try:
        state = agent.get_state(config)
        msgs = list((state.values or {}).get("messages") or [])
    except Exception as e:  # noqa: BLE001 — repair is best-effort
        logger.warning("could not read state to repair thread: %s", e)
        return

    bad_ids: set[str] = set()
    drop: list[Any] = []
    for m in msgs:
        calls = getattr(m, "tool_calls", None) or []
        # A call the provider could not even JSON-decode ("Extra data: line 1
        # column 85") lands in invalid_tool_calls, not tool_calls, so checking
        # only tool_calls found nothing to drop, left the thread untouched, and
        # the retry re-sent the same poisoned history and failed identically.
        invalid = getattr(m, "invalid_tool_calls", None) or []
        if invalid or (calls and any(not isinstance(c.get("args"), dict) for c in calls)):
            drop.append(m)
            for c in [*calls, *invalid]:
                if c.get("id"):
                    bad_ids.add(str(c["id"]))
    # ToolMessages answering a dropped call would now be orphaned.
    for m in msgs:
        if getattr(m, "tool_call_id", None) and str(m.tool_call_id) in bad_ids:
            drop.append(m)

    if not drop:
        logger.warning("malformed-tool-args 400 but no malformed message found")
        return
    try:
        from langchain_core.messages import RemoveMessage

        agent.update_state(
            config,
            {"messages": [RemoveMessage(id=m.id) for m in drop if getattr(m, "id", None)]},
        )
        logger.warning("repaired thread: dropped %d malformed message(s)", len(drop))
    except Exception as e:  # noqa: BLE001
        logger.warning("thread repair failed: %s", e)


def _customer_words(agent: Any, config: dict[str, Any], user_message: str) -> str:
    """Everything the customer has said in this thread, this message last.

    The search tools refuse a departure city the customer never said. Checking
    only the latest message refused the city they gave one message earlier:
    "now show me business class for the same trip" was answered with a
    NeedsCustomerInput for origin_city, and the model, holding a city it knew
    was right and a refusal it could not explain, told the customer it had
    "lost track of our previous conversation".
    """
    said: list[str] = []
    try:
        state = agent.get_state(config)
        for m in (state.values or {}).get("messages") or []:
            text = _message_content(m)
            if _message_role(m) in {"human", "user"} and isinstance(text, str):
                said.append(_customer_part(text))
    except Exception:  # noqa: BLE001 -- no history is a fresh conversation
        pass
    said.append(user_message)
    return "\n".join(said)


def invoke_and_log(
    agent: Any,
    *,
    surface: str,
    thread_id: str,
    user_message: str,
    turn_number: int = 0,
) -> dict[str, Any]:
    # Injection guard runs in code, not in the prompt: a matched message never
    # reaches the model, never enters the checkpoint, and costs zero tokens.
    from rules import (
        detect_prompt_injection,
        injection_reply,
        set_conversation_text,
        set_current_thread,
    )

    # Tools need the customer's own words to tell an invented departure
    # city from a real one -- all of them, not just this message.
    set_current_thread(thread_id)
    set_conversation_text(
        _customer_words(agent, {"configurable": {"thread_id": thread_id}}, user_message))

    category = detect_prompt_injection(user_message)
    if category:
        from langchain_core.messages import AIMessage, HumanMessage

        logging.getLogger("agent.turn").warning(
            "injection blocked category=%s surface=%s thread=%s",
            category, surface, thread_id,
        )
        return {"messages": [HumanMessage(content=user_message),
                             AIMessage(content=injection_reply())]}

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
    try:
        response = agent.invoke(
            {"messages": [{"role": "user", "content": user_message}]}, config
        )
    except Exception as e:  # noqa: BLE001 — we re-raise unless it is the known 400
        if not _is_malformed_tool_args(e):
            raise
        _repair_thread(agent, config)
        response = agent.invoke(
            {"messages": [{"role": "user", "content": user_message}]}, config
        )
    latency = time.perf_counter() - start
    model_reply = _final_reply(response)
    _apply_reply_guards(response)
    if _final_reply(response) != model_reply:
        _save_final_reply(agent, config, response)
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
    response = _recover_empty_turn(agent, response, config,
                                   surface=surface, thread_id=thread_id)
    all_msgs = list(response.get("messages") or [])
    retry = _research_unsourced(agent, config, all_msgs, _this_turn(all_msgs),
                                thread_id=thread_id)
    if retry is not None:
        before = _final_reply(retry)
        _apply_reply_guards(retry)
        if _final_reply(retry) != before:
            _save_final_reply(agent, config, retry)
        response = retry
    _guard_empty_turn(response, surface=surface, thread_id=thread_id)
    return response


def _apply_reply_guards(response: dict[str, Any]) -> None:
    """Every correction a finished reply gets, in one place for both paths.

    These only ran in invoke_and_log, so the streaming path -- the Next.js chat
    the client tested -- showed invented fares and dead PDF links that the
    Streamlit path would have caught.
    """
    _replace_invented_results(response)
    _strip_pdf_links(response)
    _correct_unbuilt_pdf_claim(response)
    _enforce_visa_timeline(response)
    _ensure_visa_party_total(response)


def _ensure_visa_party_total(response: dict[str, Any]) -> None:
    """A party of 2+ is told what the visa costs them all, not one adult fare.

    The cards show per-person prices. Asked for "2 adults and 1 child", the
    reply gave only the Express family total and never the Normal one
    (Rs 17,713.08), though the tool returned it. When the reply carries no
    party total for the first (cheapest) visa option, the tool's own line is
    added.
    """
    turn = _this_turn((response or {}).get("messages") or [])
    first: dict[str, Any] | None = None
    for call in extract_tool_calls({"messages": turn}):
        if str(call.get("tool_name") or "") != "get_visa_info":
            continue
        out = _coerce_to_dict(call.get("output"))
        opts = (out or {}).get("options") or []
        if opts and isinstance(opts[0], dict) and opts[0].get("party_totals"):
            first = opts[0]
    if not first:
        return
    normal = next((t for t in first["party_totals"] if t.get("track") == "Normal"), None)
    if not normal or "1 adult (" in str(normal.get("display")):
        return
    text = _final_reply(response)
    amount = f"{float(normal['total_inr']):,.2f}"
    # Indian grouping in the display ("17,713.08" either way for 5 digits);
    # check the display's own digits so 1,77,130.80 also matches.
    shown = str(normal.get("display") or "").split(" for ")[0].lstrip("₹")
    if not text or shown in text or amount in text:
        return
    _set_final_reply(
        response,
        f"{text.rstrip()}\n\n**Total, {first.get('visa_type') or 'visa'}:** {normal['display']}",
    )


# A promise about a visa's outcome or timing. Approval is UAE immigration's
# decision, so none of these can be honest.
_VISA_GUARANTEE = re.compile(
    r"\b(definitely|certainly|surely|guarantee[ds]?|no problem|for sure)\b", re.IGNORECASE
)


def _enforce_visa_timeline(response: dict[str, Any]) -> None:
    """Make the visa-timing answer match get_visa_info's timeline.

    Asked on 16 Oct about a 20 Oct flight, with the tool's timeline saying one
    working day was left and neither track was sure to finish, the model
    replied "Yes, your visa can definitely be ready in time" -- the report's
    original finding, unchanged by a prompt rule. So in code: when this turn
    looked a visa up, any sentence promising readiness or approval is removed,
    and when time is tight the tool's own note is added if the reply lacks it.
    """
    turn = _this_turn((response or {}).get("messages") or [])
    timeline: dict[str, Any] | None = None
    for call in extract_tool_calls({"messages": turn}):
        if str(call.get("tool_name") or call.get("name") or "") != "get_visa_info":
            continue
        out = _coerce_to_dict(call.get("output"))
        if isinstance(out, dict) and isinstance(out.get("timeline"), dict):
            timeline = out["timeline"]
    if not timeline or not timeline.get("note"):
        return
    text = _final_reply(response)
    if not text:
        return
    # Drop just the offending sentences, in place, so tables and line breaks
    # in the rest of the reply survive.
    fixed = text
    for m in reversed(list(re.finditer(r"[^.!?\n]*[.!?]", text))):
        s = m.group(0)
        if _VISA_GUARANTEE.search(s) and re.search(r"ready|in time|approv|visa", s, re.I):
            fixed = fixed[:m.start()] + fixed[m.end():]
    fixed = re.sub(r"[ \t]{2,}", " ", fixed).strip()
    tight = timeline.get("recommended_track") != "Normal"
    note = str(timeline["note"])
    if tight and note[:40] not in fixed and "may not finish" not in fixed and "Neither" not in fixed:
        fixed = f"**Timing:** {note}\n\n{fixed}"
    if fixed != text:
        _set_final_reply(response, fixed)


def _final_reply(response: dict[str, Any]) -> str:
    """The customer-facing text of the turn's last assistant message."""
    for m in reversed((response or {}).get("messages") or []):
        c = getattr(m, "content", None)
        if (isinstance(c, str) and c.strip() and m.__class__.__name__ == "AIMessage"
                and not getattr(m, "tool_calls", None) and not is_internal_marker(c)):
            return c
    return ""


def _this_turn(messages: list[Any]) -> list[Any]:
    """Messages produced after the latest human message.

    invoke() returns the whole thread, so "did this turn call a tool" has to be
    asked of this slice, not of the full history.
    """
    for i in range(len(messages) - 1, -1, -1):
        if _message_role(messages[i]) in {"human", "user"}:
            return list(messages[i + 1:])
    return list(messages)


# A reply announcing a lookup it never made. Asked for business-class options,
# the model answered "One moment while I pull those premium options for you"
# with no tool call and ended the turn; the customer waited for nothing and had
# to ask again.
_PROMISE = re.compile(
    r"\b(one moment|just a moment|give me a (moment|second|minute)|hold on|"
    r"let me (now |quickly )?(check|pull|search|find|look|get|fetch)|"
    r"i(?:'ll| will) (now |quickly )?(check|pull|search|find|look|get|fetch)|"
    r"(pulling|fetching|checking|searching) (up |for |those |the )?)",
    re.IGNORECASE,
)

# A lookup offered on the customer's answer is not a broken promise: "Just let
# me know which one interests you, and I'll pull up the timings" waits for them
# by design. Retrying it appended a second "which one?" answer to the first.
_WAITS_FOR_CUSTOMER = re.compile(
    r"\b(let me know|tell me|once you|if you|when you|which (one|flight|hotel|option)|"
    r"you('d| would) like|your (pick|choice)|pick one|choose)\b",
    re.IGNORECASE,
)

_ACT_NOW_MARKER = (
    "[system] Your last reply said you would look something up, but no search "
    "ran. If you have what you need, call the tools now and answer from their "
    "results in this reply. If you need something from the customer first, ask "
    "for it in one question. Do not announce the search again."
)


def _is_unfulfilled_promise(response: dict[str, Any]) -> bool:
    """True when the reply ENDS on a promised lookup and shows no results.

    Ending on the promise is what makes it unfulfilled: "here are your flights
    — let me know if you want hotels" is a finished answer. Tool calls alone do
    not settle it: told a 10-person search was over the 9-passenger limit (and
    with the party tool having run fine), the model replied "I'll search for a
    group of 9 first ... One moment while I pull those options." and ended.
    """
    turn = _this_turn((response or {}).get("messages") or [])
    for call in extract_tool_calls({"messages": turn}):
        out = _coerce_to_dict(call.get("output"))
        if isinstance(out, dict) and not out.get("error") and out.get("options"):
            return False
    text = _final_reply({"messages": turn}).strip()
    if not text:
        return False
    last = re.split(r"(?<=[.!?])\s+", text)[-1]
    return bool(_PROMISE.search(last)) and not _WAITS_FOR_CUSTOMER.search(last)


# What the customer sees when a nudged retry still produced nothing to stand on.
_COULD_NOT_FINISH = (
    "I couldn't complete that search just now. Please ask me again and I'll run "
    "it straight away."
)


def _retry_is_grounded(response: dict[str, Any]) -> bool:
    """A nudged retry may be shown only if it looked something up or asked.

    Nudged to finish a promised 10-passenger search, the model wrote a table of
    flights that do not exist -- with no tool call behind it. A reply with no
    lookup is acceptable only as a question back to the customer.
    """
    turn = _this_turn((response or {}).get("messages") or [])
    if extract_tool_calls({"messages": turn}):
        return True
    return _final_reply({"messages": turn}).rstrip().endswith("?")


def _save_final_reply(agent: Any, config: dict[str, Any], response: dict[str, Any]) -> None:
    """Write the (possibly corrected) last reply back into the checkpoint.

    The guards rewrite the reply the customer sees; without this the thread kept
    the original, and the next turn read its own invented flights as history.
    A message with the same id replaces the stored one.
    """
    for m in reversed((response or {}).get("messages") or []):
        if m.__class__.__name__ == "AIMessage" and not getattr(m, "tool_calls", None):
            if getattr(m, "id", None):
                try:
                    agent.update_state(config, {"messages": [m]})
                except Exception as e:  # noqa: BLE001 — the reply shown is already right
                    logging.getLogger("agent.turn").warning("could not save corrected reply: %s", e)
            return


def _set_final_reply(response: dict[str, Any], text: str) -> None:
    """Replace the last assistant message's text in place."""
    for m in reversed((response or {}).get("messages") or []):
        c = getattr(m, "content", None)
        if (isinstance(c, str) and m.__class__.__name__ == "AIMessage"
                and not getattr(m, "tool_calls", None) and not is_internal_marker(c)):
            m.content = text
            return


def _research_unsourced(agent: Any, config: dict[str, Any], history: list[Any],
                        turn: list[Any], *, thread_id: str) -> dict[str, Any] | None:
    """Re-run the search when a tool-less reply quoted prices nobody returned.

    Returns the retry's response, or None when the reply was fine. A retry that
    still looks nothing up is not shown: the customer is asked to send the
    request again rather than read a second set of made-up fares.
    """
    bad = _unsourced_prices(history, turn)
    if not bad:
        return None
    log = logging.getLogger("agent.turn")
    log.warning("reply had no search behind it (%s) — re-searching thread=%s",
                ", ".join(bad[:5]), thread_id)
    try:
        retry = agent.invoke({"messages": [{"role": "user", "content": _RESEARCH_MARKER}]},
                             config)
    except Exception:  # noqa: BLE001 — fall back to the safe message below
        retry = None
    if retry is None or not extract_tool_calls(
            {"messages": _this_turn(list(retry.get("messages") or []))}):
        log.warning("re-search made no lookup — not shown thread=%s", thread_id)
        if retry is None:
            # Rewritten in place; the caller's own messages now carry it.
            _set_final_reply({"messages": turn}, _COULD_NOT_FINISH)
            return {"messages": list(history) or list(turn), "in_place": True}
        _set_final_reply(retry, _COULD_NOT_FINISH)
        _save_final_reply(agent, config, retry)
    return retry


def _turn_is_dead(response: dict[str, Any]) -> bool:
    """No assistant text AND no tool calls — the model emitted nothing."""
    msgs = (response or {}).get("messages") or []
    for m in reversed(msgs):
        c = getattr(m, "content", None)
        if isinstance(c, str) and c.strip() and m.__class__.__name__ == "AIMessage":
            return False
    return not (extract_tool_calls(response) or [])


def _has_prose(response: dict[str, Any]) -> bool:
    for m in reversed((response or {}).get("messages") or []):
        c = getattr(m, "content", None)
        if (isinstance(c, str) and c.strip() and m.__class__.__name__ == "AIMessage"
                and not getattr(m, "tool_calls", None) and not is_internal_marker(c)):
            return True
    return False


_WRITE_UP_MARKER = (
    "[system] The search results ARE in this conversation but no reply was "
    "written. Write the customer's reply now from those results — do not "
    "re-search, and quote only figures the results contain."
)


def _recover_empty_turn(agent: Any, response: dict[str, Any], config: dict[str, Any],
                        *, surface: str, thread_id: str) -> dict[str, Any]:
    """Retry up to twice when the model emitted no usable reply.

    Certain phrasings ("2 adults 2 kids ages 6 and 9...") reproducibly make the
    model return an empty completion; a nudge recovers it. Stage 1 nudges it to
    search, stage 2 to write up results it already has. A reply that only
    promises a lookup it never made is not a usable reply either.
    """
    log = logging.getLogger("agent.turn")
    for _ in range(2):
        promised = _is_unfulfilled_promise(response)
        if _has_prose(response) and not promised:
            return response
        if promised:
            nudge = _ACT_NOW_MARKER
        else:
            nudge = _NO_DATA_MARKER if _turn_is_dead(response) else _WRITE_UP_MARKER
        log.warning("empty turn — nudge retry surface=%s thread=%s", surface, thread_id)
        try:
            retry = agent.invoke({"messages": [{"role": "user", "content": nudge}]}, config)
        except Exception:  # noqa: BLE001 — keep what we have on retry failure
            return response
        if promised and not _retry_is_grounded(retry):
            log.warning("nudged retry made no lookup — not shown thread=%s", thread_id)
            _set_final_reply(retry, _COULD_NOT_FINISH)
            _save_final_reply(agent, config, retry)
            return retry
        response = retry
    return response



# A turn that produced no assistant text AND no tool calls retrieved nothing.
# Left alone it becomes a silent hole in the thread, and the model fills that
# hole on the next turn by inventing data. Put an explicit marker in history so
# the next turn can see that nothing was retrieved.
# Prefix that marks a message as internal guidance, never shown to a customer.
INTERNAL_MARKER_PREFIX = "[system]"

# Graph steps per tool round: pre_model_hook, model, post_model_hook, tools.
_STEPS_PER_TOOL_ROUND = 4
# Tool rounds a chat turn may take before the backstop ends it.
PLAN_TOOL_ROUNDS = 8


def _customer_part(text: str) -> str:
    """What the customer typed, without a leading `[system]` context block.

    The API prepends remembered facts to a session's first message
    ("[system] Known about this customer ...", a blank line, then their
    message). Dropping every marker-led message threw the customer's own words
    away with it: "Burj Khalifa on 22 October" was forgotten, and "the same
    day" on the next turn was refused for having no date. A marker with no
    customer text after it yields "".
    """
    # Several context blocks can lead one message (remembered facts, then the
    # Plan mode instruction), so strip them all.
    while is_internal_marker(text):
        _, sep, text = text.partition("\n\n")
        if not sep:
            return ""
    return text


def is_internal_marker(text: object) -> bool:
    """True if this content is an internal guidance marker, not customer text."""
    return isinstance(text, str) and text.lstrip().startswith(INTERNAL_MARKER_PREFIX)


_NO_DATA_MARKER = (
    "[system] The previous turn produced no reply and retrieved no data. "
    "NOTHING has been searched yet in this conversation. Do not describe any "
    "hotel, flight, tour or price until a tool has actually returned it — "
    "call the search tools now and quote only what they return."
)


def _guard_empty_turn(response: dict[str, Any], *, surface: str, thread_id: str) -> None:
    """Stamp a no-data marker onto a turn that returned neither text nor tools."""
    import logging

    logger = logging.getLogger("agent.turn")
    msgs = (response or {}).get("messages") or []
    text = ""
    for m in reversed(msgs):
        c = getattr(m, "content", None)
        if isinstance(c, str) and c.strip() and m.__class__.__name__ == "AIMessage":
            text = c
            break
    if text.strip():
        return
    calls = extract_tool_calls(response) or []
    logger.warning(
        "empty assistant reply surface=%s thread=%s tools=%s",
        surface, thread_id, [c.get("tool_name") for c in calls],
    )
    if calls:
        # Tools ran, prose is missing. Name what was retrieved so the next turn
        # writes it up from history rather than re-searching or inventing.
        got = sorted({str(c.get("tool_name") or "") for c in calls if c.get("tool_name")})
        missing_hotels = not any("hotel" in n for n in got)
        note = (
            "[system] The previous turn called " + ", ".join(got) + " and their "
            "results ARE in this conversation, but no reply was written. Write "
            "that reply now from those results — do not re-search what you "
            "already have, and quote only figures those results contain."
        )
        if missing_hotels:
            note += (" Hotels were NOT searched — call search_hotels before "
                     "mentioning any hotel, rate or cancellation policy.")
        try:
            from langchain_core.messages import AIMessage

            msgs.append(AIMessage(content=note))
        except Exception as e:  # noqa: BLE001 — a marker is best-effort
            logger.debug("could not append salvage marker: %s", e)
        return
    try:
        from langchain_core.messages import AIMessage

        msgs.append(AIMessage(content=_NO_DATA_MARKER))
    except Exception as e:  # noqa: BLE001 — a marker is best-effort
        logger.debug("could not append no-data marker: %s", e)


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
        # Called whenever a node finishes (a tool was chosen, a tool returned).
        # Text tokens are yielded, but a turn spends most of its time in tools
        # and yields nothing then; this is how a caller shows each step the
        # moment it happens instead of when the next token arrives.
        self.on_update: Callable[[], None] | None = None

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

    # Same code-level injection guard as invoke_and_log: Streamlit streams, so
    # without this the model would already be leaking before any post-check.
    from rules import (
        detect_prompt_injection,
        injection_reply,
        set_conversation_text,
        set_current_thread,
    )

    set_current_thread(thread_id)

    set_conversation_text(
        _customer_words(agent, {"configurable": {"thread_id": thread_id}}, user_message))

    if detect_prompt_injection(user_message):
        from langchain_core.messages import AIMessage, HumanMessage

        reply = injection_reply()
        holder.text = reply
        holder.messages = [HumanMessage(content=user_message),
                           AIMessage(content=reply)]
        logging.getLogger("agent.turn").warning(
            "injection blocked (stream) surface=%s thread=%s", surface, thread_id
        )
        yield reply
        return

    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if surface == "voice":
        config["recursion_limit"] = 25
    else:
        # Backstop against tool-loops (some models re-call the same search 5-7×,
        # hanging the turn for 90s). Counted in graph steps, and one tool round
        # is FOUR of them since the pre/post model hooks were added (hook,
        # model, hook, tools): the old 12 allowed only three rounds, so
        # lookup -> options -> details hit the cap and the customer saw a raw
        # "Recursion limit of 12 reached". Eight rounds, plus the final answer:
        # a built package (hotel, tours, transfers, airport legs, the plan)
        # used six and was cut off before its reply.
        config["recursion_limit"] = _STEPS_PER_TOOL_ROUND * PLAN_TOOL_ROUNDS + 1
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
            if holder.on_update is not None:
                try:
                    holder.on_update()
                except Exception:  # progress is cosmetic; never fail the turn over it
                    logging.getLogger("agent.turn").debug("on_update failed", exc_info=True)
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
        corrupt = "tool_calls" in str(_ve) and "ToolMessage" in str(_ve)
        # The provider's 400 on a tool call it could not parse reached voice and
        # WhatsApp as a raw traceback, because only invoke_and_log handled it.
        # Same failure, same repair — drop the unparseable turn and retry once.
        if not (corrupt or _is_malformed_tool_args(_ve)):
            raise
        if corrupt:
            _clear_thread()
        else:
            _repair_thread(agent, config)
        holder.text = ""
        holder.messages = []
        for mode, chunk in agent.stream(
            {"messages": [{"role": "user", "content": user_message}]},
            config,
            stream_mode=["messages", "updates"],
        ):
            yield from _process_chunk(mode, chunk)

    # Same recovery as invoke_and_log: a turn that emitted nothing, or only
    # promised a lookup it never made, is retried once with a nudge. The
    # promise has already streamed, so the real answer is appended to it.
    dead = _turn_is_dead(holder.response)
    if dead or _is_unfulfilled_promise(holder.response):
        logging.getLogger("agent.turn").warning(
            "%s (stream) — retrying once thread=%s",
            "empty turn" if dead else "unfulfilled promise", thread_id,
        )
        try:
            retry = agent.invoke(
                {"messages": [{"role": "user",
                               "content": _NO_DATA_MARKER if dead else _ACT_NOW_MARKER}]},
                config,
            )
            if not dead and not _retry_is_grounded(retry):
                logging.getLogger("agent.turn").warning(
                    "nudged retry made no lookup — not shown thread=%s", thread_id)
                _set_final_reply(retry, _COULD_NOT_FINISH)
                _save_final_reply(agent, config, retry)
            reply = _final_reply({"messages": _this_turn(list(retry.get("messages") or []))})
            if reply:
                holder.messages = [*holder.messages,
                                   *_this_turn(list(retry.get("messages") or []))]
                joined = f"\n\n{reply}" if holder.text else reply
                holder.text += joined
                yield joined
        except Exception:  # noqa: BLE001 — the surface fallback handles it
            pass

    # A reply with no search behind it may only quote prices some tool returned.
    # The invented one has already streamed; `done` carries the replacement.
    retry = _research_unsourced(agent, config, _thread_messages(agent, config),
                                _this_turn(list(holder.messages)), thread_id=thread_id)
    if retry is not None and retry.get("in_place"):
        holder.text = _COULD_NOT_FINISH
    elif retry is not None:
        fresh = _this_turn(list(retry.get("messages") or []))
        holder.messages = [*holder.messages, *fresh]
        reply = _final_reply({"messages": fresh})
        if reply:
            holder.text = reply

    # Corrections apply here too; the caller sends `holder.text` as the final
    # reply so a corrected answer replaces what streamed.
    last_before = _final_reply(holder.response)
    _apply_reply_guards(holder.response)
    last_after = _final_reply(holder.response)
    if last_after != last_before:
        _save_final_reply(agent, config, holder.response)
        holder.text = (holder.text.replace(last_before, last_after)
                       if last_before and last_before in holder.text else last_after)

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
    r"|get_tour_options|get_tour_timeslots|get_tour_details"
    # Both call shapes leak: name{...} and name(arg:value). A live run emitted
    # `get_tour_options(tour_id:28488,travel_date:<|"|>...)<tool_call|>`.
    r"|[a-z_]+_tool)\s*[\{\(]",
    re.IGNORECASE,
)
# Chat-template control markers some models emit as plain text.
_TOOL_MARKER_RE = re.compile(r"<\|?tool_call\|?>|<\|\"\|>|<\|im_(start|end)\|>")


def _looks_like_tool_scaffolding(token: str) -> bool:
    """True if a streamed token is leaked tool-call syntax, not a real reply."""
    t = token.strip()
    if not t:
        return False
    return (bool(_TOOL_NAMES_RE.search(t)) or bool(_TOOL_MARKER_RE.search(t))
            or t.startswith("//"))


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
