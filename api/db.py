"""Postgres connection pool and migration runner for the chat API.

Separate from the LangGraph checkpointer on purpose. LangGraph owns its own
tables and its own connection; this pool serves our readable transcript, session
list and auth. Sharing one pool between them would mean a checkpoint write
could exhaust the connections the UI needs to render a page.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

# The rest of the app reads config through pydantic-settings, which loads .env
# itself. This module and api/auth.py read os.environ directly (they need plain
# strings, not a settings model), so without this a local `uvicorn` run sees
# none of DATABASE_URL / SESSION_SECRET / CORS_ORIGINS even though they are
# sitting in .env, and fails with "DATABASE_URL is not set".
#
# override=False: a real environment variable always wins, so Railway's
# injected values are never shadowed by a stray .env inside the image.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # python-dotenv absent in a minimal deploy image
    pass

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_pool: ConnectionPool | None = None


def database_url() -> str:
    """The Postgres URL, or a clear error naming what to set.

    Railway exposes two: DATABASE_URL uses `postgres.railway.internal`, which
    resolves ONLY inside Railway, and DATABASE_PUBLIC_URL goes via the TCP
    proxy. Running migrations from a laptop needs the public one — the internal
    hostname fails DNS resolution with a bare "could not translate host name"
    that says nothing about why.
    """
    url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    # Railway's Postgres image is postgres-ssl, and psycopg defaults to
    # sslmode=prefer which silently falls back to plaintext -- over the public
    # TCP proxy that would send the password unencrypted across the internet.
    # So require TLS for remote hosts.
    #
    # Local containers are exempt: a plain `postgres:alpine` has no TLS, and
    # forcing sslmode=require there fails the connection outright with "server
    # does not support SSL" -- turning the dev database into a dead end.
    if url and "sslmode=" not in url:
        is_local = any(h in url for h in ("@localhost", "@127.0.0.1", "@host.docker.internal"))
        if not is_local:
            url += ("&" if "?" in url else "?") + "sslmode=require"
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. On Railway reference the Postgres service "
            "(DATABASE_URL=${{Postgres.DATABASE_URL}}); from a local machine use "
            "DATABASE_PUBLIC_URL, which requires Public Networking enabled on "
            "the Postgres service."
        )
    return url


def get_pool() -> ConnectionPool:
    """Process-wide pool, opened on first use."""
    global _pool
    if _pool is None:
        # min_size=1 so a cold container does not pay connection setup on the
        # first request; max_size is deliberately modest because Railway's
        # Postgres plans cap connections and the agent's own fan-out already
        # holds several.
        _pool = ConnectionPool(
            database_url(),
            min_size=1,
            max_size=10,
            timeout=10.0,
            open=True,
            # Verify a connection before handing it out. Railway recycles
            # containers, and a pooled connection to a restarted Postgres fails
            # on first use rather than on checkout without this.
            check=ConnectionPool.check_connection,
        )
        logger.info("postgres pool opened")
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("postgres pool closed")


def run_migrations() -> list[str]:
    """Apply every migrations/*.sql in filename order. Returns what ran.

    The .sql files are written to be idempotent (CREATE ... IF NOT EXISTS,
    CREATE OR REPLACE), so re-running on every boot is safe and there is no
    version table to drift out of sync with reality.
    """
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError(f"no migrations found in {MIGRATIONS_DIR}")

    applied: list[str] = []
    with get_pool().connection() as conn:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            conn.execute(sql)
            applied.append(path.name)
            logger.info("migration applied: %s", path.name)
    return applied


def healthcheck() -> dict[str, object]:
    """Confirm the DB answers and report what we are actually connected to."""
    with get_pool().connection() as conn:
        version = conn.execute("SELECT version()").fetchone()[0]
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            ).fetchall()
        ]
    return {"version": version.split(",")[0], "tables": tables}
