"""HTTP client for ActivityLinker.

One Session per process. Retries on 5xx and timeouts. Maps HTTP errors
to typed exceptions so callers can branch without parsing strings.
"""

from __future__ import annotations

import logging
import time
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
                if attempt > self.max_retries:
                    raise BookingApiTimeout(
                        f"Timeout for {path} after {self.max_retries + 1} attempts",
                        endpoint=path,
                    ) from e
                time.sleep(min(2**attempt, 5))
                continue
            except RequestException as e:
                last_exc = e
                if attempt > self.max_retries:
                    raise BookingApiError(
                        f"Network error calling {path}: {e}", endpoint=path
                    ) from e
                time.sleep(min(2**attempt, 5))
                continue

            sc = response.status_code
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
