"""The debug tab attached ONE http record per tool, positionally.
search_tours fires ~20 requests in a turn (toursearchlist + rates + one
Timeslot per tour + options + optionRate), so 19 were hidden and every later
tool in the turn was labelled with the wrong URL. Attribution is now by URL
path, and each request shows the exact body sent and received.
"""

import importlib.util
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "surfaces" / "streamlit_app.py"


def _load_markers() -> dict:
    """Pull the marker map out of the app without importing streamlit."""
    src = APP.read_text(encoding="utf-8")
    block = re.search(r"_TOOL_URL_MARKERS.*?\n\}\n", src, re.S)
    assert block, "_TOOL_URL_MARKERS not found"
    ns: dict = {}
    exec(block.group(0), ns)  # noqa: S102 — reading our own source in a test
    return ns["_TOOL_URL_MARKERS"]


MARKERS = _load_markers()


def _belongs(tool: str, url: str) -> bool:
    return any(m in url for m in MARKERS.get(tool, ()))


class TestUrlAttribution:
    def test_every_tour_request_maps_to_search_tours(self):
        for path in ("/api/v1/tourservices/TourSearch/toursearchlist",
                     "/api/v1/tourservices/TourSearch/toursearchlistrate",
                     "/api/v1/tourservices/TourSearch/Timeslot",
                     "/api/tours/options",
                     "/api/tours/optionRate"):
            assert _belongs("search_tours", "https://x" + path), path

    def test_option_endpoints_map_to_get_tour_options(self):
        assert _belongs("get_tour_options", "https://x/api/tours/options")
        assert _belongs("get_tour_options", "https://x/api/tours/optionRate")

    def test_tools_do_not_claim_unrelated_urls(self):
        assert not _belongs("search_flights", "https://x/api/tours/options")
        assert not _belongs("search_tours", "https://x/api/Flight/search")
        assert not _belongs("search_hotels", "https://x/api/visa/v1/visas")

    def test_each_api_backed_tool_has_markers(self):
        src = APP.read_text(encoding="utf-8")
        block = re.search(r"_API_BACKED_TOOLS = \{(.*?)\}", src, re.S)
        assert block
        # Strip trailing comments — one mentions a tool name that is not a member.
        body = "\n".join(ln.split("#")[0] for ln in block.group(1).splitlines())
        names = re.findall(r'"([a-z_]+)"', body)
        missing = [n for n in names if n not in MARKERS]
        assert not missing, f"API-backed tools with no URL markers: {missing}"


class TestHttpLogCarriesPayloads:
    def test_recorder_signature_accepts_both_bodies(self):
        # The debug UI needs the exact payloads; assert the recorder takes them.
        # (Round-trip behaviour is covered in test_http_recorder.py, which owns
        # the process-wide log and its seq counter.)
        import inspect

        from booking_api.http_client import record_http_request

        params = inspect.signature(record_http_request).parameters
        assert "request_body" in params
        assert "response_body" in params


class TestNewToolsAreTrackedAsApiBacked:
    def test_get_tour_options_is_api_backed(self):
        src = APP.read_text(encoding="utf-8")
        block = re.search(r"_API_BACKED_TOOLS = \{(.*?)\}", src, re.S).group(1)
        assert '"get_tour_options"' in block
        assert '"get_tour_timeslots"' in block
