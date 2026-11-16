"""Admin panel API: users, searches, chat transcripts and usage, read-only.

Admins are the accounts whose email is listed in ADMIN_EMAILS (comma
separated, case-insensitive). Every route depends on `require_admin`, which
answers 404 to everyone else, signed in or not, so the panel's existence is
not advertised. Reading a customer's chat is written to admin_audit.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import admin_repository as repo
from . import auth

router = APIRouter(prefix="/admin-api", tags=["admin"])


def admin_emails() -> set[str]:
    """Read on every call, so a changed env var needs no code path to reload."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    email = str(user.get("email") or "").strip().lower()
    return bool(email) and email in admin_emails()


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


def require_admin(request: Request) -> dict[str, Any]:
    """The signed-in admin, else 404 (also when not signed in at all)."""
    try:
        user = auth.current_user(request)
    except HTTPException:
        raise _not_found() from None
    if not is_admin(user):
        raise _not_found()
    return user


def _uuid_or_404(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError):
        raise _not_found() from None


@router.get("/overview")
def overview(
    days: int = Query(30, ge=1, le=365), _admin: dict = Depends(require_admin)
) -> dict[str, Any]:
    return repo.overview(days)


@router.get("/users")
def users(
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(25, ge=1, le=100),
    _admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    return repo.list_users(q.strip(), page, page_size)


@router.get("/users/{user_id}")
def user_detail(user_id: str, _admin: dict = Depends(require_admin)) -> dict[str, Any]:
    found = repo.user_detail(_uuid_or_404(user_id))
    if not found:
        raise _not_found()
    return found


@router.get("/sessions/{session_id}/messages")
def transcript(session_id: str, admin: dict = Depends(require_admin)) -> dict[str, Any]:
    sid = _uuid_or_404(session_id)
    found = repo.session_transcript(sid)
    if not found:
        raise _not_found()
    repo.record_admin_view(str(admin["email"]), "view_transcript", sid)
    return found


@router.get("/searches")
def searches(
    tool: str = Query("", max_length=80),
    user_id: str = Query("", max_length=64),
    q: str = Query("", max_length=200),
    errors_only: bool = False,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(50, ge=1, le=200),
    _admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    return repo.list_searches(
        tool=tool.strip(),
        user_id=_uuid_or_404(user_id) if user_id.strip() else None,
        q=q.strip(),
        errors_only=errors_only,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
