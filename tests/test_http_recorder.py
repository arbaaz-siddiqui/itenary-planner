"""Tests for the live HTTP request recorder in booking_api.http_client.

The recorder is what lets the debug UI show the *actual* endpoint URL that was
hit for each tool call (the ground truth for "which API was called"). These
tests cover the cursor/since semantics and the ring-buffer cap.
"""

from __future__ import annotations

import pytest

from booking_api import http_client as hc


@pytest.fixture(autouse=True)
def _clean_log() -> None:
    hc.clear_http_request_log()
    yield
    hc.clear_http_request_log()


def _rec(url: str, status: int = 200) -> None:
    hc.record_http_request(method="GET", url=url, status_code=status, duration_ms=12.3)


def test_records_full_url_and_fields() -> None:
    _rec("https://stagingapi.gujjutours.com/api/Currency/ROE/INR")
    log = hc.get_http_request_log()
    assert len(log) == 1
    rec = log[0]
    assert rec["url"] == "https://stagingapi.gujjutours.com/api/Currency/ROE/INR"
    assert rec["method"] == "GET"
    assert rec["status_code"] == 200
    # seq is a process-wide monotonic counter and clear_http_request_log()
    # deliberately does NOT reset it: http_requests_since(cursor) and the debug
    # tab hold cursors across turns, so restarting at 1 would make a stale
    # cursor match new requests. Assert it is a positive int, not that this
    # test ran first — it only passed before because nothing preceded it.
    assert isinstance(rec["seq"], int) and rec["seq"] > 0


def test_since_cursor_returns_only_new() -> None:
    _rec("https://x/api/a")
    cursor = hc.latest_http_seq()
    _rec("https://x/api/b")
    _rec("https://x/api/c")
    new = hc.http_requests_since(cursor)
    assert [r["url"] for r in new] == ["https://x/api/b", "https://x/api/c"]


def test_latest_seq_zero_when_empty() -> None:
    assert hc.latest_http_seq() == 0


def test_error_record_has_no_status() -> None:
    hc.record_http_request(
        method="GET", url="https://x/api/down", status_code=None, duration_ms=5.0, error="timeout"
    )
    rec = hc.get_http_request_log()[-1]
    assert rec["status_code"] is None
    assert rec["error"] == "timeout"


def test_ring_buffer_caps_at_maxlen() -> None:
    for i in range(250):  # maxlen is 200
        _rec(f"https://x/api/{i}")
    log = hc.get_http_request_log()
    assert len(log) == 200
    # Oldest were evicted; newest retained.
    assert log[-1]["url"] == "https://x/api/249"
