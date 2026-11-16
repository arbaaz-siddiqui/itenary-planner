"""Admin panel API: access rule, endpoints, and the search_log migration.

The access tests need no database. The rest run against a LOCAL Postgres
(DATABASE_URL on 127.0.0.1 / localhost, e.g. the gt_pg container) and are
skipped when there is none, so the suite stays green on a laptop without it.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from api import admin


# ------------------------------------------------------------- access rule
def test_is_admin_reads_env_case_insensitively(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", " Boss@Example.com , ops@example.com,")
    assert admin.is_admin({"email": "boss@example.COM"})
    assert admin.is_admin({"email": "ops@example.com"})
    assert not admin.is_admin({"email": "customer@example.com"})
    assert not admin.is_admin({"email": ""})
    assert not admin.is_admin(None)
    monkeypatch.setenv("ADMIN_EMAILS", "")
    assert not admin.is_admin({"email": "boss@example.com"})


def _probe_app(user):
    """A tiny app whose only route is guarded by require_admin."""

    def fake_current_user(_request: Request):
        if user is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return user

    app = FastAPI()

    @app.get("/admin-api/probe")
    def probe(who: dict = Depends(admin.require_admin)):
        return {"email": who["email"]}

    return app, fake_current_user


@pytest.mark.parametrize(
    "user, expected",
    [
        (None, 404),
        ({"id": "1", "email": "customer@example.com"}, 404),
        ({"id": "2", "email": "Boss@example.com"}, 200),
    ],
)
def test_require_admin_hides_the_panel_from_everyone_else(monkeypatch, user, expected):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    app, fake = _probe_app(user)
    monkeypatch.setattr(admin.auth, "current_user", fake)
    res = TestClient(app).get("/admin-api/probe")
    assert res.status_code == expected
    if expected == 404:
        assert res.json() == {"detail": "Not Found"}


# --------------------------------------------------------- database tests
def _local_db_url() -> str | None:

    url = os.environ.get("DATABASE_URL") or ""
    return url if any(h in url for h in ("@127.0.0.1", "@localhost")) else None


@pytest.fixture(scope="module")
def pg():
    if not _local_db_url():
        pytest.skip("no local Postgres in DATABASE_URL")
    if not os.environ.get("SESSION_SECRET"):
        pytest.skip("SESSION_SECRET not set")
    from api import db

    try:
        db.run_migrations()
    except Exception as e:
        pytest.skip(f"local Postgres unreachable: {e}")
    yield db
    db.close_pool()


@pytest.fixture(scope="module")
def seeded(pg):
    """An admin, a customer with one chat, and a reply saved BEFORE search_log
    rows existed for it (what the backfill finds on first deploy)."""
    from api import repository

    tag = uuid.uuid4().hex[:8]
    admin_user = repository.create_user(f"admin-{tag}@example.com", "x", "Ada", "Admin")
    customer = repository.create_user(f"cust-{tag}@example.com", "x", "Cyrus", "Customer")
    session = repository.create_session(str(customer["id"]), "Dubai in December")
    repository.add_message(str(session["id"]), "user", "Flights Mumbai to Dubai 10 Dec for 2")
    calls = [
        {"tool_name": "search_flights",
         "input": {"origin_city": "Mumbai", "destination_city": "Dubai",
                   "departure_date": "2026-12-10", "adults": 2},
         "output": json.dumps({"options": [{"airline": "Emirates"}] * 4})},
        {"tool_name": "search_hotels",
         "input": {"destination_city": f"Atlantis{tag}", "check_in": "2026-12-10", "adults": 2},
         "output": json.dumps({"error": True, "message": "Supplier timeout"})},
    ]
    reply = repository.add_message(
        str(session["id"]), "assistant", "Here are 4 flights.", calls,
        [{"kind": "flights", "items": [{"airline": "Emirates", "price_inr": 41000}]}],
    )
    yield {"tag": tag, "admin": admin_user, "customer": customer, "session": session,
           "reply": reply, "calls": calls}
    with pg.get_pool().connection() as conn:
        conn.execute("DELETE FROM admin_audit WHERE admin_email = %s", (admin_user["email"],))
        conn.execute("DELETE FROM users WHERE id = ANY(%s)",
                     ([admin_user["id"], customer["id"]],))


def _rows_for(pg, message_id) -> list[dict]:
    from psycopg.rows import dict_row

    with pg.get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT call_index, tool, origin, destination, start_date, party, result_count, "
            "error FROM search_log WHERE message_id = %s ORDER BY call_index",
            (message_id,),
        )
        return cur.fetchall()


def test_backfill_matches_python_and_reruns_add_nothing(pg, seeded):
    from api import admin_repository
    from api.search_log import search_rows

    mid = seeded["reply"]["id"]
    assert _rows_for(pg, mid) == []  # saved through repository, not the turn
    pg.run_migrations()
    pg.run_migrations()  # idempotent: the second run neither fails nor duplicates
    got = _rows_for(pg, mid)
    keys = ("call_index", "tool", "origin", "destination", "start_date", "party",
            "result_count", "error")
    want = [{k: r[k] for k in keys} for r in search_rows(seeded["calls"])]
    assert got == want
    # A live write of the same reply is skipped too.
    admin_repository.add_search_rows(
        str(seeded["customer"]["id"]), str(seeded["session"]["id"]), str(mid),
        search_rows(seeded["calls"]),
    )
    assert len(_rows_for(pg, mid)) == 2


def _client(monkeypatch, user) -> TestClient:
    from api import auth
    from api.main import app

    client = TestClient(app)
    if user is not None:
        token = auth._sign(str(user["id"]), int(time.time()) + 600)
        client.cookies.set(auth.COOKIE_NAME, token)
    return client


def test_endpoints_for_admin_and_404_for_others(pg, seeded, monkeypatch):
    pg.run_migrations()
    monkeypatch.setenv("ADMIN_EMAILS", seeded["admin"]["email"].upper())
    customer, sid = seeded["customer"], str(seeded["session"]["id"])

    anon = _client(monkeypatch, None)
    cust = _client(monkeypatch, customer)
    boss = _client(monkeypatch, seeded["admin"])

    for c in (anon, cust):
        for path in ("/admin-api/overview", "/admin-api/users",
                     f"/admin-api/sessions/{sid}/messages", "/admin-api/searches"):
            assert c.get(path).status_code == 404, path

    assert cust.get("/auth/me").json()["user"]["is_admin"] is False
    assert boss.get("/auth/me").json()["user"]["is_admin"] is True

    ov = boss.get("/admin-api/overview?days=7").json()
    assert ov["days"] == 7 and len(ov["daily"]) == 7
    assert ov["kpis"]["total_users"] >= 2
    assert ov["kpis"]["searches"] >= 2 and ov["kpis"]["failed_searches"] >= 1
    assert any(t["tool"] == "search_flights" for t in ov["searches_by_tool"])
    assert any(f["error"] == "Supplier timeout" for f in ov["recent_failed"])
    assert sum(d["turns"] for d in ov["daily"]) == ov["kpis"]["turns"]

    users = boss.get("/admin-api/users", params={"q": f"cust-{seeded['tag']}"}).json()
    assert users["total"] == 1
    [u] = users["users"]
    assert (u["chats"], u["messages"], u["searches"]) == (1, 2, 2)
    assert u["last_active"]

    detail = boss.get(f"/admin-api/users/{customer['id']}").json()
    assert detail["user"]["email"] == customer["email"]
    assert detail["sessions"][0]["messages"] == 2
    assert detail["searches"] == 2
    assert boss.get(f"/admin-api/users/{uuid.uuid4()}").status_code == 404
    assert boss.get("/admin-api/users/not-a-uuid").status_code == 404

    tr = boss.get(f"/admin-api/sessions/{sid}/messages").json()
    assert [m["role"] for m in tr["messages"]] == ["user", "assistant"]
    assert tr["messages"][1]["options"][0]["kind"] == "flights"
    assert tr["messages"][1]["tool_calls"][0]["tool_name"] == "search_flights"
    with pg.get_pool().connection() as conn:
        n = conn.execute(
            "SELECT count(*) FROM admin_audit WHERE admin_email = %s AND target = %s",
            (seeded["admin"]["email"], sid),
        ).fetchone()[0]
    assert n == 1

    s = boss.get("/admin-api/searches",
                 params={"user_id": str(customer["id"]), "errors_only": "true"}).json()
    assert s["total"] == 1 and s["searches"][0]["error"] == "Supplier timeout"
    s = boss.get("/admin-api/searches", params={"q": f"atlantis{seeded['tag']}"}).json()
    assert s["total"] == 1 and s["searches"][0]["tool"] == "search_hotels"
    s = boss.get("/admin-api/searches",
                 params={"tool": "search_flights", "user_id": str(customer["id"]),
                         "from": "2020-01-01", "to": "2099-01-01"}).json()
    assert s["total"] == 1 and s["searches"][0]["party"] == "2 adults"
    s = boss.get("/admin-api/searches",
                 params={"user_id": str(customer["id"]), "to": "2020-01-01"}).json()
    assert s["total"] == 0
    assert boss.get("/admin-api/searches", params={"user_id": "nope"}).status_code == 404
