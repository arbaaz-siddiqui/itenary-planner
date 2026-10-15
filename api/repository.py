"""Session and message persistence for the chat API.

Plain SQL over psycopg rather than an ORM: the queries are few and simple, and
an ORM would add a dependency and a mapping layer to save nothing here.

Nothing in this module touches LangGraph's checkpoint tables. Those hold live
agent state and are owned by PostgresSaver; these tables are the readable
transcript. Keeping the two apart means a LangGraph upgrade cannot break chat
history, and a change here cannot corrupt agent state.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from psycopg.rows import dict_row

from .db import get_pool


# ------------------------------------------------------------------ users
def create_user(
    email: str, password_hash: str, first_name: str = "", last_name: str = ""
) -> dict[str, Any]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name) "
            "VALUES (%s, %s, %s, %s) "
            "RETURNING id, email, first_name, last_name, created_at",
            (email.strip(), password_hash, first_name.strip(), last_name.strip()),
        )
        return cur.fetchone()


def update_user_name(user_id: str, first_name: str, last_name: str) -> dict[str, Any] | None:
    """Set the customer's name. Accounts made before names were asked for have
    empty ones, and the home screen greets by first name."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "UPDATE users SET first_name = %s, last_name = %s WHERE id = %s "
            "RETURNING id, email, first_name, last_name, created_at",
            (first_name.strip(), last_name.strip(), user_id),
        )
        return cur.fetchone()


def find_user_by_email(email: str) -> dict[str, Any] | None:
    # lower(email) matches the unique index, so "A@x.com" finds "a@x.com"
    # instead of creating a second account with a split history.
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, email, first_name, last_name, password_hash, created_at FROM users "
            "WHERE lower(email) = lower(%s)",
            (email.strip(),),
        )
        return cur.fetchone()


def find_user_by_id(user_id: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, email, first_name, last_name, created_at FROM users WHERE id = %s",
            (user_id,),
        )
        return cur.fetchone()


# --------------------------------------------------------------- sessions
def create_session(user_id: str, title: str = "New chat") -> dict[str, Any]:
    """New chat session with its own LangGraph thread id.

    The thread id is generated here, not by the caller, so a session can never
    be created without one — two sessions sharing a thread would merge two
    customers' conversations into a single agent context.
    """
    thread_id = f"web_{uuid.uuid4().hex}"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO chat_sessions (user_id, title, thread_id) "
            "VALUES (%s, %s, %s) "
            "RETURNING id, title, thread_id, created_at, updated_at",
            (user_id, title, thread_id),
        )
        return cur.fetchone()


def list_sessions(user_id: str) -> list[dict[str, Any]]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, title, thread_id, created_at, updated_at "
            "FROM chat_sessions WHERE user_id = %s "
            "ORDER BY updated_at DESC",
            (user_id,),
        )
        return cur.fetchall()


def get_session(session_id: str, user_id: str) -> dict[str, Any] | None:
    """One session, scoped to its owner.

    user_id is part of the WHERE clause, not checked afterwards: a caller who
    forgets the ownership check gets no row rather than someone else's chat.
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, user_id, title, thread_id, created_at, updated_at "
            "FROM chat_sessions WHERE id = %s AND user_id = %s",
            (session_id, user_id),
        )
        return cur.fetchone()


def rename_session(session_id: str, user_id: str, title: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "UPDATE chat_sessions SET title = %s WHERE id = %s AND user_id = %s "
            "RETURNING id, title, thread_id, created_at, updated_at",
            (title.strip() or "New chat", session_id, user_id),
        )
        return cur.fetchone()


def delete_session(session_id: str, user_id: str) -> bool:
    # Messages go with it via ON DELETE CASCADE.
    with get_pool().connection() as conn:
        cur = conn.execute(
            "DELETE FROM chat_sessions WHERE id = %s AND user_id = %s",
            (session_id, user_id),
        )
        return cur.rowcount > 0


# --------------------------------------------------------------- messages
def add_message(
    session_id: str,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Append one message. An AFTER INSERT trigger bumps the session's
    updated_at, so the sidebar reorders without a second write to remember."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO chat_messages (session_id, role, content, tool_calls, options) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, role, content, created_at",
            (session_id, role, content, json.dumps(tool_calls or []),
             json.dumps(options or [])),
        )
        return cur.fetchone()


def list_messages(session_id: str, include_tool_calls: bool = False) -> list[dict[str, Any]]:
    """Transcript, oldest first.

    tool_calls is excluded by default. Those payloads reach 200KB+ for a tour
    search, so loading them with every transcript render would make opening a
    long chat slow for data the debug panel shows only on demand.
    """
    # `options` IS loaded with the transcript, unlike tool_calls: the cards are
    # part of the reply the customer is reading, and without them every tour
    # image disappeared on reload while the text stayed.
    cols = "id, role, content, created_at, options, feedback"
    if include_tool_calls:
        cols += ", tool_calls"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {cols} FROM chat_messages WHERE session_id = %s "
            "ORDER BY created_at",
            (session_id,),
        )
        return cur.fetchall()


def set_feedback(message_id: str, user_id: str, value: int | None) -> bool:
    """Rate one assistant reply in one of the user's own chats. False if not theirs."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE chat_messages m SET feedback = %s FROM chat_sessions s "
            "WHERE m.id = %s AND m.session_id = s.id AND s.user_id = %s "
            "AND m.role = 'assistant'",
            (value, message_id, user_id),
        )
        return cur.rowcount == 1


def delete_last_reply(session_id: str) -> None:
    """Remove the newest assistant reply of a chat, before it is regenerated.

    Without this a regenerated answer was stored next to the one it replaced,
    and the question once per attempt, so a reload showed both.
    """
    with get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM chat_messages WHERE id = ("
            "SELECT id FROM chat_messages WHERE session_id = %s AND role = 'assistant' "
            "ORDER BY created_at DESC LIMIT 1)",
            (session_id,),
        )


def get_message_tool_calls(message_id: str) -> list[dict[str, Any]]:
    """The debug payload for one message, fetched only when the panel opens."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT tool_calls FROM chat_messages WHERE id = %s", (message_id,)
        ).fetchone()
    return row[0] if row else []


# ------------------------------------------------------------ user memory
def get_memory(user_id: str) -> dict[str, Any]:
    """Durable facts carried between sessions, so a returning customer is not
    re-asked their departure city every time."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT facts FROM user_memory WHERE user_id = %s", (user_id,)
        ).fetchone()
    return row[0] if row else {}


def merge_memory(user_id: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge new facts over old.

    `||` merges server-side in one statement rather than read-modify-write, so
    two concurrent turns cannot clobber each other's facts.
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO user_memory (user_id, facts) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE "
            "SET facts = user_memory.facts || EXCLUDED.facts "
            "RETURNING facts",
            (user_id, json.dumps(facts)),
        )
        return cur.fetchone()["facts"]
