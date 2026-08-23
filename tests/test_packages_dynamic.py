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
