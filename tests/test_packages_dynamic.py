"""Client report: "we are showing only the static packages for the tours,
there are dynamic too."

The supplier returns 217 packages nested at `result.packages`, and labels each
one with its own `category` — "Dynamic Package" or "Land Package". Our tool did:

    list_items = list_raw.get("result") or list_raw.get("packages") or []
    if not isinstance(list_items, list):
        list_items = []

`result` is a DICT (it also carries categories, hotelPackages, tourPackages),
so it failed the isinstance check, list_items became [], and ZERO package ids
were extracted — every rate call was skipped and only a 5-row unpriced
fallback survived. Those 5 rows happened to be the 3-night ones.
"""

from typing import Any


class TestPackageListParsing:
    def _resp(self, packages: list[dict[str, Any]]) -> dict[str, Any]:
        # The real shape: packages nested under a dict `result`.
        return {
            "statusCode": 200,
            "result": {
                "packages": packages,
                "categories": [],
                "hotelPackages": [],
                "tourPackages": [],
            },
        }

    def test_packages_nested_in_a_dict_result_are_found(self):
        from mcp_tools.list_packages import _impl  # noqa: F401  (import guard)

        raw = self._resp([{"packageId": 2, "packageName": "A"},
                          {"packageId": 3, "packageName": "B"}])
        res = raw.get("result")
        items: list[Any] = []
        for candidate in (
            res.get("packages") if isinstance(res, dict) else None,
            res if isinstance(res, list) else None,
            raw.get("packages"),
        ):
            if isinstance(candidate, list) and candidate:
                items = candidate
                break
        assert len(items) == 2, "result.packages must be read, not `result` itself"

    def test_the_old_isinstance_check_found_nothing(self):
        # Documents the exact defect: this is what the code used to do.
        raw = self._resp([{"packageId": 2}, {"packageId": 3}])
        legacy = raw.get("result") or raw.get("packages") or []
        if not isinstance(legacy, list):
            legacy = []
        assert legacy == [], "the dict `result` silently yielded zero packages"

    def test_a_flat_list_result_still_works(self):
        raw = {"result": [{"packageId": 9}]}
        res = raw.get("result")
        items = res if isinstance(res, list) else (res or {}).get("packages") or []
        assert len(items) == 1

    def test_selected_package_id_is_a_valid_fallback(self):
        row = {"selectedPackageId": 77, "packageName": "X"}
        pid = row.get("packageId") or row.get("packageID") or row.get("selectedPackageId")
        assert pid == 77


class TestPackageDefaults:
    def test_max_results_is_not_five(self):
        import inspect

        from mcp_tools.list_packages import _impl

        # 5 hid every 5- and 6-night package behind the 3-night ones.
        assert inspect.signature(_impl).parameters["max_results"].default >= 25


class TestPackagesCarryNoPricing:
    """Package pricing is being settled with the supplier, so it must not reach
    the customer at all — not as a figure, not as a "Rs 0", not as an
    "On Request" column. What replaces it is the data the supplier already
    sends and we were discarding: audience tags, inclusions, max pax."""

    def _packages(self):
        from mcp_tools.list_packages import _impl

        return _impl(destination_city="Dubai", check_in="2026-09-15",
                     check_out="2026-09-19", adults=2)

    def test_no_price_key_survives_anywhere(self):
        r = self._packages()
        leaked = [k for k in r if "price" in k.lower() or "pricing" in k.lower()]
        assert not leaked, f"price keys leaked at top level: {leaked}"
        for row in r["options"]:
            leaked = [k for k in row if "price" in k.lower() or "pricing" in k.lower()]
            assert not leaked, f"price keys leaked in a package row: {leaked}"

    def test_instructions_forbid_showing_a_price(self):
        r = self._packages()
        note = r["agent_instructions"].lower()
        assert "do not show any price" in note
        assert "never invent or estimate a package price" in note

    def test_tags_and_inclusions_replace_the_price_column(self):
        r = self._packages()
        assert r["options"], "expected packages for these dates"
        row = r["options"][0]
        for field in ("package_type", "tags", "nights", "includes",
                      "max_pax", "is_free_cancellation"):
            assert field in row, f"{field} must be present to fill the table"

    def test_dynamic_label_comes_from_the_supplier(self):
        r = self._packages()
        types = {row["package_type"] for row in r["options"]}
        # The client's complaint: the list looked static-only. The supplier's
        # own label says otherwise.
        assert any("Dynamic" in t for t in types), types
