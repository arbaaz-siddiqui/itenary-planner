"""SQL for the admin panel and for writing search_log rows.

The admin views are the one place that reads across accounts on purpose, so
every function here is called only behind `api.admin.require_admin`. All of
it is read-only except `add_search_rows` (written by the chat API when a turn
is saved) and `record_admin_view` (the audit trail of transcript reads).
Every value reaches SQL as a parameter, never by string formatting; the only
formatted parts are fixed fragments chosen in this module.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from .db import get_pool

# Days are counted in India time: the customers and the team are there, and a
# UTC day would split an Indian evening across two bars.
TZ = "Asia/Kolkata"


# ----------------------------------------------------------------- writes
def add_search_rows(
    user_id: str, session_id: str, message_id: str, rows: list[dict[str, Any]]
) -> int:
    """Insert one reply's searches. A row already written is skipped."""
    if not rows:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO search_log (user_id, session_id, message_id, call_index, tool, "
            "args, origin, destination, start_date, party, result_count, error, latency_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (message_id, call_index) DO NOTHING",
            [
                (
                    user_id, session_id, message_id, r["call_index"], r["tool"],
                    json.dumps(r.get("args") or {}), r.get("origin"), r.get("destination"),
                    r.get("start_date"), r.get("party"), r.get("result_count"),
                    r.get("error"), r.get("latency_ms"),
                )
                for r in rows
            ],
        )
        return len(rows)


def record_admin_view(admin_email: str, action: str, target: str) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO admin_audit (admin_email, action, target) VALUES (%s, %s, %s)",
            (admin_email, action, target),
        )


# --------------------------------------------------------------- overview
def overview(days: int) -> dict[str, Any]:
    """KPIs and daily series for the last `days` days (today included)."""
    since_sql = "(date_trunc('day', now() AT TIME ZONE %(tz)s) - make_interval(days => %(days)s - 1)) AT TIME ZONE %(tz)s"
    p = {"tz": TZ, "days": days}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            WITH since AS (SELECT {since_sql} AS t)
            SELECT
              (SELECT count(*) FROM users) AS total_users,
              (SELECT count(*) FROM users, since WHERE created_at >= since.t) AS new_users,
              (SELECT count(DISTINCT s.user_id) FROM chat_messages m
                 JOIN chat_sessions s ON s.id = m.session_id, since
                 WHERE m.role = 'user' AND m.created_at >= since.t) AS active_users,
              (SELECT count(*) FROM chat_sessions, since WHERE created_at >= since.t) AS chats,
              (SELECT count(*) FROM chat_messages, since
                 WHERE role = 'user' AND created_at >= since.t) AS turns,
              (SELECT count(*) FROM search_log, since WHERE created_at >= since.t) AS searches,
              (SELECT count(*) FROM search_log, since
                 WHERE created_at >= since.t AND error IS NOT NULL) AS failed_searches,
              (SELECT count(*) FROM chat_messages, since
                 WHERE feedback = 1 AND created_at >= since.t) AS thumbs_up,
              (SELECT count(*) FROM chat_messages, since
                 WHERE feedback = -1 AND created_at >= since.t) AS thumbs_down
            """,
            p,
        )
        kpis = {k: int(v or 0) for k, v in cur.fetchone().items()}

        cur.execute(
            f"""
            WITH days AS (
              SELECT generate_series(
                (date_trunc('day', now() AT TIME ZONE %(tz)s) - make_interval(days => %(days)s - 1))::date,
                (now() AT TIME ZONE %(tz)s)::date,
                interval '1 day')::date AS day
            ),
            since AS (SELECT {since_sql} AS t),
            turns AS (
              SELECT (m.created_at AT TIME ZONE %(tz)s)::date AS day,
                     count(*) AS turns, count(DISTINCT s.user_id) AS active_users
              FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id, since
              WHERE m.role = 'user' AND m.created_at >= since.t GROUP BY 1
            ),
            signups AS (
              SELECT (created_at AT TIME ZONE %(tz)s)::date AS day, count(*) AS signups
              FROM users, since WHERE created_at >= since.t GROUP BY 1
            ),
            searches AS (
              SELECT (created_at AT TIME ZONE %(tz)s)::date AS day, count(*) AS searches,
                     count(*) FILTER (WHERE error IS NOT NULL) AS failed
              FROM search_log, since WHERE created_at >= since.t GROUP BY 1
            )
            SELECT d.day,
                   COALESCE(t.active_users, 0) AS active_users,
                   COALESCE(u.signups, 0) AS signups,
                   COALESCE(t.turns, 0) AS turns,
                   COALESCE(s.searches, 0) AS searches,
                   COALESCE(s.failed, 0) AS failed_searches
            FROM days d
            LEFT JOIN turns t ON t.day = d.day
            LEFT JOIN signups u ON u.day = d.day
            LEFT JOIN searches s ON s.day = d.day
            ORDER BY d.day
            """,
            p,
        )
        daily = [
            {**{k: int(v) for k, v in r.items() if k != "day"}, "day": r["day"].isoformat()}
            for r in cur.fetchall()
        ]

        cur.execute(
            f"""
            WITH since AS (SELECT {since_sql} AS t)
            SELECT tool, count(*) AS searches,
                   count(*) FILTER (WHERE error IS NOT NULL) AS failed
            FROM search_log, since WHERE created_at >= since.t
            GROUP BY tool ORDER BY searches DESC, tool LIMIT 30
            """,
            p,
        )
        by_tool = [
            {"tool": r["tool"], "searches": int(r["searches"]), "failed": int(r["failed"])}
            for r in cur.fetchall()
        ]

        cur.execute(
            f"""
            WITH since AS (SELECT {since_sql} AS t)
            SELECT min(destination) AS destination, count(*) AS searches
            FROM search_log, since
            WHERE created_at >= since.t AND destination IS NOT NULL AND destination <> ''
            GROUP BY lower(destination) ORDER BY searches DESC, 1 LIMIT 10
            """,
            p,
        )
        top_destinations = [
            {"destination": r["destination"], "searches": int(r["searches"])}
            for r in cur.fetchall()
        ]

        cur.execute(
            f"""
            WITH since AS (SELECT {since_sql} AS t)
            SELECT l.id, l.user_id, u.email, l.session_id, l.tool, l.destination,
                   l.start_date, l.error, l.created_at
            FROM search_log l JOIN users u ON u.id = l.user_id, since
            WHERE l.created_at >= since.t AND l.error IS NOT NULL
            ORDER BY l.created_at DESC LIMIT 10
            """,
            p,
        )
        failed = [_search_out(r) for r in cur.fetchall()]

    return {
        "days": days,
        "kpis": kpis,
        "daily": daily,
        "searches_by_tool": by_tool,
        "top_destinations": top_destinations,
        "recent_failed": failed,
    }


# ------------------------------------------------------------------ users
def list_users(q: str, page: int, page_size: int) -> dict[str, Any]:
    """Accounts, most recently active first, with their activity counts."""
    where = ""
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if q:
        where = (
            "WHERE u.email ILIKE %(q)s OR u.first_name ILIKE %(q)s OR u.last_name ILIKE %(q)s "
            "OR (u.first_name || ' ' || u.last_name) ILIKE %(q)s"
        )
        params["q"] = f"%{_escape_like(q)}%"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT count(*) AS n FROM users u {where}", params)
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"""
            SELECT u.id, u.email, u.first_name, u.last_name, u.created_at,
                   act.last_active, act.messages,
                   (SELECT count(*) FROM chat_sessions s WHERE s.user_id = u.id) AS chats,
                   (SELECT count(*) FROM search_log l WHERE l.user_id = u.id) AS searches
            FROM users u
            LEFT JOIN LATERAL (
              SELECT max(m.created_at) FILTER (WHERE m.role = 'user') AS last_active,
                     count(*) AS messages
              FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id
              WHERE s.user_id = u.id
            ) act ON true
            {where}
            ORDER BY act.last_active DESC NULLS LAST, u.created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        rows = cur.fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "users": [
            {
                "id": str(r["id"]),
                "email": r["email"],
                "first_name": r["first_name"] or "",
                "last_name": r["last_name"] or "",
                "created_at": r["created_at"].isoformat(),
                "last_active": r["last_active"].isoformat() if r["last_active"] else None,
                "chats": int(r["chats"]),
                "messages": int(r["messages"] or 0),
                "searches": int(r["searches"]),
            }
            for r in rows
        ],
    }


def user_detail(user_id: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, email, first_name, last_name, created_at FROM users WHERE id = %s",
            (user_id,),
        )
        user = cur.fetchone()
        if not user:
            return None
        cur.execute("SELECT facts FROM user_memory WHERE user_id = %s", (user_id,))
        mem = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM saved_cards WHERE user_id = %s", (user_id,))
        saved = int(cur.fetchone()["n"])
        cur.execute(
            """
            SELECT s.id, s.title, s.pinned, s.created_at, s.updated_at,
                   (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS messages,
                   (SELECT count(*) FROM search_log l WHERE l.session_id = s.id) AS searches
            FROM chat_sessions s WHERE s.user_id = %s ORDER BY s.updated_at DESC
            """,
            (user_id,),
        )
        sessions = cur.fetchall()
        cur.execute(
            "SELECT count(*) AS n, max(created_at) AS last FROM search_log WHERE user_id = %s",
            (user_id,),
        )
        srch = cur.fetchone()
        # Renaming or pinning a chat also moves its updated_at, so activity
        # is the customer's last message, not the newest chat.
        cur.execute(
            "SELECT max(m.created_at) AS last FROM chat_messages m "
            "JOIN chat_sessions s ON s.id = m.session_id "
            "WHERE s.user_id = %s AND m.role = 'user'",
            (user_id,),
        )
        last_active = cur.fetchone()["last"]
    facts = (mem or {}).get("facts") or {}
    return {
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "first_name": user["first_name"] or "",
            "last_name": user["last_name"] or "",
            "created_at": user["created_at"].isoformat(),
        },
        "facts": {k: v for k, v in facts.items() if v not in (None, "")},
        "saved_cards": saved,
        "searches": int(srch["n"]),
        "last_search": srch["last"].isoformat() if srch["last"] else None,
        "last_active": last_active.isoformat() if last_active else None,
        "sessions": [
            {
                "id": str(s["id"]),
                "title": s["title"],
                "pinned": bool(s["pinned"]),
                "created_at": s["created_at"].isoformat(),
                "updated_at": s["updated_at"].isoformat(),
                "messages": int(s["messages"]),
                "searches": int(s["searches"]),
            }
            for s in sessions
        ],
    }


# ------------------------------------------------------------- transcript
_OUTPUT_PREVIEW_CHARS = 2000


def _tool_call_out(call: Any) -> dict[str, Any]:
    """A stored tool call, with its (often 200KB) output cut to a preview."""
    if not isinstance(call, dict):
        return {"tool_name": "unknown", "input": {}, "output_preview": str(call)[:200]}
    out = call.get("output")
    text = out if isinstance(out, str) else json.dumps(out, default=str)
    return {
        "tool_name": str(call.get("tool_name") or call.get("name") or "unknown"),
        "input": call.get("input") or {},
        "output_preview": text[:_OUTPUT_PREVIEW_CHARS],
        "output_chars": len(text),
    }


def session_transcript(session_id: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, s.user_id, u.email, "
            "u.first_name, u.last_name FROM chat_sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.id = %s",
            (session_id,),
        )
        session = cur.fetchone()
        if not session:
            return None
        cur.execute(
            "SELECT id, role, content, created_at, options, feedback, tool_calls "
            "FROM chat_messages WHERE session_id = %s ORDER BY created_at",
            (session_id,),
        )
        messages = cur.fetchall()
    return {
        "session": {
            "id": str(session["id"]),
            "title": session["title"],
            "created_at": session["created_at"].isoformat(),
            "updated_at": session["updated_at"].isoformat(),
        },
        "user": {
            "id": str(session["user_id"]),
            "email": session["email"],
            "first_name": session["first_name"] or "",
            "last_name": session["last_name"] or "",
        },
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "created_at": m["created_at"].isoformat(),
                "options": m.get("options") or [],
                "feedback": {1: "up", -1: "down"}.get(m.get("feedback")),
                "tool_calls": [_tool_call_out(c) for c in (m.get("tool_calls") or [])],
            }
            for m in messages
        ],
    }


# --------------------------------------------------------------- searches
def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_out(r: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": str(r["id"]),
        "user_id": str(r["user_id"]),
        "email": r.get("email"),
        "session_id": str(r["session_id"]) if r.get("session_id") else None,
        "tool": r["tool"],
        "destination": r.get("destination"),
        "start_date": r.get("start_date"),
        "error": r.get("error"),
        "created_at": r["created_at"].isoformat(),
    }
    for key in ("origin", "party", "result_count", "latency_ms", "args"):
        if key in r:
            out[key] = r[key]
    return out


def list_searches(
    *,
    tool: str = "",
    user_id: str | None = None,
    q: str = "",
    errors_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """search_log rows, newest first, with the customer's email."""
    clauses: list[str] = []
    params: dict[str, Any] = {"tz": TZ, "limit": page_size, "offset": (page - 1) * page_size}
    if tool:
        clauses.append("l.tool = %(tool)s")
        params["tool"] = tool
    if user_id:
        clauses.append("l.user_id = %(user_id)s")
        params["user_id"] = user_id
    if q:
        clauses.append(
            "(l.destination ILIKE %(q)s OR l.origin ILIKE %(q)s OR u.email ILIKE %(q)s "
            "OR l.error ILIKE %(q)s OR l.args::text ILIKE %(q)s)"
        )
        params["q"] = f"%{_escape_like(q)}%"
    if errors_only:
        clauses.append("l.error IS NOT NULL")
    if date_from:
        clauses.append("l.created_at >= (%(date_from)s::date)::timestamp AT TIME ZONE %(tz)s")
        params["date_from"] = date_from
    if date_to:
        clauses.append(
            "l.created_at < ((%(date_to)s::date + 1)::timestamp AT TIME ZONE %(tz)s)"
        )
        params["date_to"] = date_to
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM search_log l JOIN users u ON u.id = l.user_id {where}",
            params,
        )
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"""
            SELECT l.id, l.user_id, u.email, l.session_id, l.tool, l.args, l.origin,
                   l.destination, l.start_date, l.party, l.result_count, l.error,
                   l.latency_ms, l.created_at
            FROM search_log l JOIN users u ON u.id = l.user_id
            {where}
            ORDER BY l.created_at DESC, l.call_index DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        rows = [_search_out(r) for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT tool FROM search_log ORDER BY tool")
        tools = [r["tool"] for r in cur.fetchall()]
    return {"total": total, "page": page, "page_size": page_size, "searches": rows, "tools": tools}
