"""HTTP client for ActivityLinker.

One Session per process. Retries on 5xx and timeouts. Maps HTTP errors
to typed exceptions so callers can branch without parsing strings.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from functools import lru_cache
from typing import Any

import requests
from requests.exceptions import RequestException, Timeout

from core import (
    BookingApiError,
    BookingApiNotFound,
    BookingApiServerError,
    BookingApiTimeout,
    BookingApiUnauthorized,
)
from settings import get_booking_api_settings, get_http_settings

logger = logging.getLogger(__name__)


# =============================================================================
# Live HTTP request recorder
# =============================================================================
# Every outbound supplier request is recorded here (method + FULL url + status +
# duration). The debug UI reads this so you can see the *actual* endpoint that
# was hit for a given tool call — e.g. the literal
# `GET https://stagingapi.gujjutours.com//api/Currency/ROE/INR` — not just the
# tool name. This is the ground truth for "which API was called", which is the
# surest way to catch a hallucinated number (no request = invented).
_RECORDER_LOCK = threading.Lock()
_REQUEST_LOG: deque[dict[str, Any]] = deque(maxlen=200)
_REQUEST_SEQ = itertools.count(1)


def record_http_request(
    *, method: str, url: str, status_code: int | None, duration_ms: float, error: str | None = None
) -> None:
    """Append one HTTP request record to the in-memory log (thread-safe)."""
    with _RECORDER_LOCK:
        _REQUEST_LOG.append(
            {
                "seq": next(_REQUEST_SEQ),
                "method": method,
                "url": url,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 1),
                "error": error,
            }
        )


def get_http_request_log() -> list[dict[str, Any]]:
    """Snapshot of recorded requests, oldest first."""
    with _RECORDER_LOCK:
        return list(_REQUEST_LOG)


def http_requests_since(seq: int) -> list[dict[str, Any]]:
    """All recorded requests with seq strictly greater than `seq`.

    Lets a caller mark a cursor before invoking the agent, then collect exactly
    the requests that fired during that turn.
    """
    with _RECORDER_LOCK:
        return [r for r in _REQUEST_LOG if r["seq"] > seq]


def latest_http_seq() -> int:
    """Highest seq recorded so far (0 if none) — use as a cursor."""
    with _RECORDER_LOCK:
        return _REQUEST_LOG[-1]["seq"] if _REQUEST_LOG else 0


def clear_http_request_log() -> None:
    with _RECORDER_LOCK:
        _REQUEST_LOG.clear()


class BookingApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        tenant_id: str,
        timeout_secs: int = 60,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.tenant_id = tenant_id
        self.timeout_secs = timeout_secs
        self.max_retries = max_retries
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path)
        attempt = 0
        last_exc: Exception | None = None

        while attempt <= self.max_retries:
            attempt += 1
            req_start = time.perf_counter()
            try:
                logger.info(
                    "booking_api request",
                    extra={"method": method, "path": path, "attempt": attempt},
                )
                response = self.session.request(
                    method=method,
                    url=url,
                    json=json,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_secs,
                )
            except Timeout as e:
                last_exc = e
                record_http_request(
                    method=method,
                    url=url,
                    status_code=None,
                    duration_ms=(time.perf_counter() - req_start) * 1000,
                    error="timeout",
                )
                if attempt > self.max_retries:
                    raise BookingApiTimeout(
                        f"Timeout for {path} after {self.max_retries + 1} attempts",
                        endpoint=path,
                    ) from e
                time.sleep(min(2**attempt, 5))
                continue
            except RequestException as e:
                last_exc = e
                record_http_request(
                    method=method,
                    url=url,
                    status_code=None,
                    duration_ms=(time.perf_counter() - req_start) * 1000,
                    error=type(e).__name__,
                )
                if attempt > self.max_retries:
                    raise BookingApiError(
                        f"Network error calling {path}: {e}", endpoint=path
                    ) from e
                time.sleep(min(2**attempt, 5))
                continue

            sc = response.status_code
            record_http_request(
                method=method,
                url=url,
                status_code=sc,
                duration_ms=(time.perf_counter() - req_start) * 1000,
            )
            if sc == 401:
                raise BookingApiUnauthorized(
                    f"401 Unauthorized for {path}",
                    endpoint=path,
                    status_code=sc,
                    server_message=_server_msg(response),
                )
            if sc == 404:
                raise BookingApiNotFound(
                    f"404 Not Found for {path}",
                    endpoint=path,
                    status_code=sc,
                    server_message=_server_msg(response),
                )
            if 500 <= sc < 600:
                if attempt > self.max_retries:
                    raise BookingApiServerError(
                        f"{sc} Server error for {path}",
                        endpoint=path,
                        status_code=sc,
                        server_message=_server_msg(response),
                    )
                time.sleep(min(2**attempt, 5))
                continue
            if 400 <= sc < 500:
                raise BookingApiError(
                    f"{sc} Client error for {path}",
                    endpoint=path,
                    status_code=sc,
                    server_message=_server_msg(response),
                )

            body = _parse_body(response) or {}

            # Many endpoints use a body-level envelope `{"statusCode": <int>, ...}`
            # that can disagree with the HTTP status (e.g. TransferList returns
            # HTTP 200 with body `{"statusCode": 404, "result": []}` when no
            # transfers match the search). Log these so they're discoverable.
            if isinstance(body, dict):
                inner = body.get("statusCode")
                if isinstance(inner, int) and inner >= 400:
                    logger.warning(
                        "booking_api soft-error",
                        extra={
                            "path": path,
                            "http_status": sc,
                            "body_status": inner,
                            "body_error": body.get("error"),
                        },
                    )
            return body

        raise BookingApiError(f"Exhausted retries for {path}", endpoint=path) from last_exc

    def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.request("POST", path, json=json, headers=headers)

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.request("GET", path, params=params, headers=headers)


def _parse_body(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()
        return body if isinstance(body, dict) else {"raw": body}
    except ValueError:
        return {"raw_text": response.text}


def _server_msg(response: requests.Response) -> str | None:
    body = _parse_body(response)
    if not isinstance(body, dict):
        return None
    for key in ("message", "Message"):
        if isinstance(body.get(key), str):
            return body[key]
    err = body.get("error") or body.get("Error")
    if isinstance(err, dict):
        return err.get("description") or err.get("message")
    if isinstance(err, str):
        return err
    return None


@lru_cache(maxsize=1)
def get_client() -> BookingApiClient:
    booking = get_booking_api_settings()
    http = get_http_settings()
    return BookingApiClient(
        base_url=booking.base_url,
        token=booking.token,
        tenant_id=booking.tenant_id,
        timeout_secs=http.timeout_secs,
        max_retries=http.max_retries,
    )


@lru_cache(maxsize=1)
def get_b2c_client() -> BookingApiClient:
    """Client for the B2C host (stagingb2c.gujjutours.com). Same token + retry
    behaviour as the main client, different base URL. Used by the B2C tour
    endpoints (options, price-check calendar, option details)."""
    booking = get_booking_api_settings()
    http = get_http_settings()
    return BookingApiClient(
        base_url=booking.b2c_base_url,
        token=booking.token,
        tenant_id=booking.tenant_id,
        timeout_secs=http.timeout_secs,
        max_retries=http.max_retries,
    )
