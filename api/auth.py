"""Login, password hashing and cookie sessions for the chat API.

Customers must be logged in before they can use the agent. That is enforced
here, in the API, and is unrelated to Railway's database networking — the DB is
never reachable by a customer under either setting.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request, Response, status

from . import repository

COOKIE_NAME = "gt_session"
SESSION_TTL_SECS = 60 * 60 * 24 * 30  # 30 days

# PBKDF2 rather than bcrypt: it is in the standard library, so there is no extra
# dependency to install on a platform build, and at this iteration count it is
# a sound choice for password storage.
_PBKDF2_ROUNDS = 390_000


def _secret() -> bytes:
    """Key for signing session cookies.

    Deliberately fails loudly when unset rather than defaulting to something
    predictable: a guessable key means anyone can forge a session cookie and
    read another customer's chats.
    """
    key = os.environ.get("SESSION_SECRET", "").strip()
    if not key:
        raise RuntimeError(
            "SESSION_SECRET is not set. Generate one with "
            "`python -c \"import secrets;print(secrets.token_urlsafe(48))\"` "
            "and set it on the chat-api service. Set it ONCE and keep it: "
            "sessions are signed with this key, so changing it silently signs "
            "out every user on the next deploy."
        )
    return key.encode()


# ------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, TypeError):
        return False
    # compare_digest, not ==, so the comparison time does not leak how much of
    # the hash matched.
    return hmac.compare_digest(dk.hex(), hash_hex)


# --------------------------------------------------------------- cookies
def _sign(user_id: str, expires_at: int) -> str:
    payload = f"{user_id}:{expires_at}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify(token: str) -> str | None:
    """The user id in a valid, unexpired cookie, else None."""
    try:
        user_id, expires_raw, sig = token.rsplit(":", 2)
        expires_at = int(expires_raw)
    except (ValueError, AttributeError):
        return None
    expected = hmac.new(
        _secret(), f"{user_id}:{expires_at}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if time.time() > expires_at:
        return None
    return user_id


def _cookie_is_secure() -> bool:
    """Whether to mark the session cookie Secure (HTTPS-only).

    True in production, and that matters: without it the session travels in
    clear text. But local development runs on plain http://localhost, where a
    Secure cookie is STORED BY THE BROWSER AND NEVER SENT BACK -- login appears
    to succeed and then every /chat call returns 401, with nothing in either
    log to explain it.

    Driven by COOKIE_SECURE when set, else inferred: any https:// origin in
    CORS_ORIGINS means we are deployed.
    """
    explicit = os.environ.get("COOKIE_SECURE", "").strip().lower()
    if explicit in {"1", "true", "yes"}:
        return True
    if explicit in {"0", "false", "no"}:
        return False
    return "https://" in os.environ.get("CORS_ORIGINS", "")


def set_session_cookie(response: Response, user_id: str) -> None:
    expires_at = int(time.time()) + SESSION_TTL_SECS
    secure = _cookie_is_secure()
    response.set_cookie(
        COOKIE_NAME,
        _sign(user_id, expires_at),
        max_age=SESSION_TTL_SECS,
        # httponly: JavaScript cannot read it, so an XSS bug cannot steal the
        # session. samesite=none is required when the web and API are on
        # different origins (as they are on Railway), and the spec only allows
        # None alongside Secure -- so locally, where Secure is off, fall back to
        # lax, which works because the dev proxy keeps the request same-origin.
        httponly=True,
        secure=secure,
        samesite="none" if secure else "lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ------------------------------------------------------------ dependency
def current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency: the logged-in user, or 401.

    Every chat route depends on this, so an unauthenticated request can never
    reach the agent or another customer's session.
    """
    token = request.cookies.get(COOKIE_NAME)
    user_id = _verify(token) if token else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in"
        )
    user = repository.find_user_by_id(user_id)
    if not user:
        # Cookie is validly signed but the account is gone (deleted user, or a
        # cookie from a previous database). Treat as signed out.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in"
        )
    return user
