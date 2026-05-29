"""audit_api_responses.py

Reads api_samples.json (produced by scripts/sample_all_apis.py) and audits
every endpoint for data quality issues. Writes ONE consolidated report to
api_audit_report.json — share that file when reporting problems.

Findings fall into 3 categories:
    SUPPLIER_BUG   — something wrong with the data the supplier sent
    PARSER_ISSUE   — something our code should handle differently
    INFO           — observation that's not necessarily wrong, but worth noting

Usage:
    # Default — reads api_samples.json from repo root, writes api_audit_report.json
    python -m scripts.audit_api_responses

    # Custom paths
    python -m scripts.audit_api_responses path/to/api_samples.json path/to/output.json

Send the resulting api_audit_report.json file when reporting issues.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLES_PATH = ROOT / "api_samples.json"
DEFAULT_REPORT_PATH = ROOT / "api_audit_report.json"


# =============================================================================
# Output helpers
# =============================================================================
def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def finding(severity: str, title: str, description: str, evidence: Any = None) -> dict:
    return {
        "severity": severity,
        "title": title,
        "description": description,
        "evidence": evidence,
    }


# =============================================================================
# Per-endpoint auditors. Each returns a list of finding dicts.
# =============================================================================
def audit_flight_search(response: dict) -> list[dict]:
    findings: list[dict] = []
    itins = response.get("data", {}).get("pricedItineraries", [])
    if not itins:
        findings.append(
            finding(
                "INFO",
                "No itineraries returned",
                "FlightSearch returned 0 itineraries for the test query.",
            )
        )
        return findings

    # 1. Currency distribution
    currs: Counter[str] = Counter()
    for it in itins:
        cc = it["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"].get("currencyCode")
        currs[cc or "MISSING"] += 1

    findings.append(
        finding(
            "INFO",
            "Itinerary count + currency distribution",
            f"{len(itins)} itineraries total across {len(currs)} currency codes.",
            evidence={"by_currency": dict(currs)},
        )
    )

    # 2. INR-labeled bogus fares (the bug we caught)
    bogus_inr = []
    for it in itins:
        tf = it["airItineraryPricingInfo"]["itinTotalFare"]["totalFare"]
        if tf.get("currencyCode") == "INR" and tf.get("amount", 0) < 2000:
            seg = it["originDestinationOptions"][0]["flightSegments"]
            bogus_inr.append(
                {
                    "amount": tf["amount"],
                    "airline": seg[0].get("marketingAirlineName"),
                    "flight_no": str(seg[0].get("marketingAirline", ""))
                    + str(seg[0].get("flightNumber", "")),
                    "route": f"{seg[0]['departureAirportLocationCode']}→{seg[-1]['arrivalAirportLocationCode']}",
                    "fare_type": it["airItineraryPricingInfo"].get("fareType") or "(empty)",
                }
            )

    if bogus_inr:
        airlines_affected = Counter(b["airline"] for b in bogus_inr)
        findings.append(
            finding(
                "SUPPLIER_BUG",
                "INR-labeled fares with implausibly low amounts",
                (
                    f"{len(bogus_inr)} itineraries have currencyCode='INR' but amounts "
                    f"below ₹2,000 — impossible for any flight. The amounts look like "
                    f"USD values mislabeled as INR. Most affected: "
                    f"{', '.join(f'{a} ({n})' for a, n in airlines_affected.most_common(3))}."
                ),
                evidence={
                    "count": len(bogus_inr),
                    "airlines_affected": dict(airlines_affected),
                    "amount_range": [
                        min(b["amount"] for b in bogus_inr),
                        max(b["amount"] for b in bogus_inr),
                    ],
                    "samples": bogus_inr[:5],
                },
            )
        )

    # 3. Wrong-destination itineraries
    # Try to infer what was requested from any USD itinerary that looks consistent
    dest_codes: Counter[str] = Counter()
    for it in itins:
        seg = it["originDestinationOptions"][0]["flightSegments"]
        dest_codes[seg[-1]["arrivalAirportLocationCode"]] += 1
    if len(dest_codes) > 1:
        most_common = dest_codes.most_common(1)[0]
        wrong = {d: n for d, n in dest_codes.items() if d != most_common[0]}
        findings.append(
            finding(
                "SUPPLIER_BUG",
                "Multiple destination airports returned",
                (
                    f"The search returned itineraries to {len(dest_codes)} different "
                    f"destination airports. The majority went to {most_common[0]} "
                    f"({most_common[1]} itineraries); the rest are: {wrong}. "
                    f"This suggests the supplier is returning related-route suggestions "
                    f"alongside the actual search, which can confuse customers."
                ),
                evidence={"destinations_seen": dict(dest_codes)},
            )
        )

    # 4. isRefundable label values
    refund_labels: Counter[str] = Counter()
    for it in itins:
        lbl = it["airItineraryPricingInfo"].get("isRefundable") or "(empty)"
        refund_labels[lbl] += 1
    findings.append(
        finding(
            "INFO",
            "isRefundable label values",
            "Values present in the isRefundable field across itineraries.",
            evidence={"labels": dict(refund_labels)},
        )
    )

    return findings


def audit_flight_details(response: dict) -> list[dict]:
    findings = []
    success = response.get("success")
    if success is False:
        errs = response.get("error") or []
        findings.append(
            finding(
                "INFO",
                "FlightDetails returned error (expected for stale fareSourceCode)",
                "Postman's hardcoded fareSourceCode is stale; this fails as expected. "
                "Verify error shape is parseable.",
                evidence={"errors": errs},
            )
        )
    return findings


def audit_hotel_search(response: dict) -> list[dict]:
    findings = []
    rs = response.get("AvailabilityRS", {})
    hotels = rs.get("HotelResult", [])
    if not hotels:
        findings.append(
            finding(
                "INFO",
                "No hotels returned",
                "HotelSearch returned 0 hotels. Check if HotelIDs in the request are valid.",
            )
        )
        return findings

    findings.append(
        finding(
            "INFO",
            "Hotel count + response currency",
            f"{len(hotels)} hotels returned. Response-level Currency='{rs.get('Currency')}'.",
            evidence={"count": len(hotels), "response_currency": rs.get("Currency")},
        )
    )

    # 1. StarRating presence
    no_stars = sum(1 for h in hotels if "StarRating" not in h and "starRating" not in h)
    if no_stars > 0:
        findings.append(
            finding(
                "SUPPLIER_BUG",
                "StarRating field missing from hotel records",
                (
                    f"{no_stars} of {len(hotels)} hotels have no StarRating field. "
                    f"Our parser defaults to 0 stars; the UI should hide stars rather "
                    f"than show '0 star'. Ask the supplier whether StarRating should be "
                    f"populated."
                ),
                evidence={"missing_count": no_stars, "total": len(hotels)},
            )
        )

    # 2. Per-room SupplierCurrency vs response-level Currency mismatch
    currency_mismatches = []
    for h in hotels:
        for opt in h.get("HotelOption", []):
            for room_group in opt.get("HotelRooms", []):
                for room in room_group:
                    sc = room.get("SupplierCurrency")
                    if sc and sc != rs.get("Currency"):
                        currency_mismatches.append(
                            {
                                "hotel_id": h.get("HotelId"),
                                "supplier_currency": sc,
                                "response_currency": rs.get("Currency"),
                            }
                        )
                        break
                else:
                    continue
                break
    if currency_mismatches:
        unique = {(m["supplier_currency"], m["response_currency"]) for m in currency_mismatches}
        findings.append(
            finding(
                "SUPPLIER_BUG",
                "Response-level Currency disagrees with per-room SupplierCurrency",
                (
                    f"{len(currency_mismatches)} room records have a SupplierCurrency "
                    f"different from the response-level Currency. Currency pairs seen: "
                    f"{unique}. The Price field is in the room's SupplierCurrency — not "
                    f"the response-level value. Our parser now correctly prefers the "
                    f"per-room currency; flagging here so you can ask the supplier why "
                    f"the two currency fields disagree at all."
                ),
                evidence={"currency_pairs": list(unique), "sample_count": len(currency_mismatches)},
            )
        )

    # 3. Cancellation policy — check for new daysBeforeCheckIn / isNRF fields
    sample_policy = None
    for h in hotels:
        for opt in h.get("HotelOption", []):
            for room_group in opt.get("HotelRooms", []):
                for room in room_group:
                    cp = room.get("CancellationPolicy", [])
                    if cp:
                        sample_policy = cp[0]
                        break
                if sample_policy:
                    break
            if sample_policy:
                break
        if sample_policy:
            break

    if sample_policy is not None:
        days_present = "daysBeforeCheckIn" in sample_policy
        nrf_present = "isNRF" in sample_policy
        findings.append(
            finding(
                "INFO",
                "Cancellation policy fields",
                f"daysBeforeCheckIn present: {days_present}, isNRF present: {nrf_present}. "
                f"Our parser captures both. Sample value of daysBeforeCheckIn: "
                f"{sample_policy.get('daysBeforeCheckIn')}.",
                evidence={"sample": sample_policy},
            )
        )

    return findings


def audit_tour_list(list_resp: dict, rate_resp: dict | None) -> list[dict]:
    findings = []
    tours = list_resp.get("result", {}).get("tourStaticlists", []) or []
    if not tours:
        findings.append(finding("INFO", "No tours returned", "TourList returned 0 tours."))
        return findings

    findings.append(
        finding(
            "INFO",
            "Tour count",
            f"{len(tours)} tours in TourList response.",
        )
    )

    # 1. Tours without matching rate
    if rate_resp:
        rate_result = rate_resp.get("result")
        if isinstance(rate_result, list):
            rate_ids = {r.get("tourID") for r in rate_result if isinstance(r, dict)}
            list_ids = {t.get("tourID") for t in tours}
            unpriced = list_ids - rate_ids
            if unpriced:
                findings.append(
                    finding(
                        "SUPPLIER_BUG",
                        "Tours present in list but missing from rates",
                        (
                            f"{len(unpriced)} of {len(list_ids)} tours appear in TourList "
                            f"but have no entry in TourListrate, meaning they have no "
                            f"bookable price. Our parser drops these rather than quoting "
                            f"₹0. Worth confirming with supplier whether these should be "
                            f"removed from the list endpoint."
                        ),
                        evidence={
                            "unpriced_count": len(unpriced),
                            "total": len(list_ids),
                            "sample_tour_ids": sorted(unpriced)[:10],
                        },
                    )
                )

    # 2. Descriptions / reviews populated
    empty_desc = sum(1 for t in tours if not (t.get("tourShortDescription") or t.get("address")))
    if empty_desc == len(tours):
        findings.append(
            finding(
                "INFO",
                "Tour list has no description fields",
                "All tours in the list endpoint have empty description/address. "
                "Descriptions are returned by the TourDetails endpoint instead. "
                "Our agent uses get_tour_details to drill in.",
            )
        )

    # 3. Rating == 0 might indicate stale or missing rating data
    zero_rating = sum(1 for t in tours if (t.get("tourrating") or 0) == 0)
    if zero_rating > len(tours) * 0.5:
        findings.append(
            finding(
                "INFO",
                "Many tours have zero rating",
                f"{zero_rating} of {len(tours)} tours have tourrating=0. "
                f"Worth confirming whether the supplier populates this field reliably.",
                evidence={"zero_rating_count": zero_rating, "total": len(tours)},
            )
        )

    return findings


def audit_visa(response: dict) -> list[dict]:
    findings = []
    visas = response.get("result", {}).get("visas", [])
    if not visas:
        findings.append(finding("INFO", "No visas returned", "VisaList returned 0 visas."))
        return findings

    total_options = sum(len(v.get("options", []) or []) for v in visas)
    findings.append(
        finding(
            "INFO",
            "Visa structure",
            f"{len(visas)} visa types with {total_options} total options.",
        )
    )

    # Check pricing availability
    priced = 0
    total_with_rates = 0
    for v in visas:
        for opt in v.get("options", []) or []:
            for rate in opt.get("visaRates", []) or []:
                total_with_rates += 1
                if rate.get("fareInfo") and len(rate["fareInfo"]) > 0:
                    priced += 1
                    break

    if total_with_rates > 0 and priced == 0:
        findings.append(
            finding(
                "INFO",
                "All visa options have empty fareInfo (no pricing)",
                (
                    "All visaRates[*].fareInfo arrays are empty. Customers see 'On Request' "
                    "for visa pricing. Confirm with supplier whether this agent should have "
                    "visa pricing enabled."
                ),
                evidence={"options_with_pricing": priced, "total_options": total_with_rates},
            )
        )

    return findings


def audit_restaurant_list(response: dict) -> list[dict]:
    findings = []
    items = response.get("result", {}).get("list", []) or []
    if not items:
        findings.append(
            finding(
                "INFO",
                "No restaurants returned",
                "RestaurantList returned 0 restaurants.",
            )
        )
        return findings

    findings.append(
        finding(
            "INFO",
            "Restaurant count",
            f"{len(items)} restaurants returned.",
        )
    )

    # Check coordinates — we saw placeholder {lat:1, lng:1}
    placeholder_coords = sum(
        1
        for r in items
        if r.get("coordinates", {}).get("latitude") == 1
        and r.get("coordinates", {}).get("longitude") == 1
    )
    if placeholder_coords > 0:
        findings.append(
            finding(
                "SUPPLIER_BUG",
                "Restaurant coordinates are placeholder values",
                (
                    f"{placeholder_coords} of {len(items)} restaurants have "
                    f"coordinates set to (lat=1, lng=1) — clearly placeholder data. "
                    f"This breaks map view and proximity calculations."
                ),
                evidence={"placeholder_count": placeholder_coords, "total": len(items)},
            )
        )

    # Currency check
    currs = Counter()
    for r in items:
        ps = r.get("priceStarts", {})
        if "currency" in ps:
            currs[ps["currency"].strip()] += 1
    findings.append(
        finding(
            "INFO",
            "Restaurant pricing currency",
            f"Currencies seen in priceStarts.currency: {dict(currs)}.",
            evidence={"by_currency": dict(currs)},
        )
    )

    return findings


def audit_transfer(response: dict) -> list[dict]:
    findings = []
    body_status = response.get("statusCode")
    result = response.get("result")

    if body_status == 404 and result == []:
        findings.append(
            finding(
                "INFO",
                "Transfer search returned empty (HTTP 200 with body statusCode=404)",
                (
                    "The transfer API uses an envelope where body.statusCode can differ "
                    "from HTTP status. Body 404 + empty result means 'no transfers for "
                    "these coordinates/dates' — not an error. Our parser handles this "
                    "correctly."
                ),
                evidence={"body_status": body_status, "result_type": "empty list"},
            )
        )
    elif isinstance(result, list) and result:
        findings.append(
            finding(
                "INFO",
                "Transfers found",
                f"{len(result)} transfer options returned.",
            )
        )
    return findings


def audit_package_list(response: dict) -> list[dict]:
    findings = []
    packages = response.get("result", {}).get("packages", []) or []
    if not packages:
        findings.append(
            finding(
                "INFO",
                "No packages returned",
                "PackageList returned 0 packages.",
            )
        )
        return findings

    findings.append(
        finding(
            "INFO",
            "Package count",
            f"{len(packages)} packages returned.",
        )
    )

    # Pricing availability
    priced = sum(1 for p in packages if (p.get("totalPrice") or 0) > 0)
    unpriced = len(packages) - priced
    if unpriced > 0:
        findings.append(
            finding(
                "INFO",
                "Packages with no totalPrice ('On Request')",
                (
                    f"{unpriced} of {len(packages)} packages have totalPrice=0 — these "
                    f"are 'On Request' packages where pricing is finalized later. "
                    f"Our parser flags pricing_available=False and sorts these last."
                ),
                evidence={
                    "unpriced_count": unpriced,
                    "priced_count": priced,
                    "total": len(packages),
                },
            )
        )

    return findings


# =============================================================================
# Main
# =============================================================================
def main() -> int:
    samples_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLES_PATH
    report_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_REPORT_PATH

    if not samples_path.exists():
        print(c(f"✗ Samples file not found: {samples_path}", "31"))
        print("  Run first:  python scripts/sample_all_apis.py")
        return 1

    print(c(f"Reading: {samples_path}", "36"))
    with samples_path.open() as f:
        samples = json.load(f)

    endpoints = samples.get("endpoints", {})

    # Run auditors per endpoint
    audit_map: dict[str, callable] = {
        "FlightSearch": lambda r: audit_flight_search(r.get("response", {})),
        "FlightList": lambda r: audit_flight_details(r.get("response", {})),
        "HotelSearch": lambda r: audit_hotel_search(r.get("response", {})),
        "TourList": lambda r: audit_tour_list(
            r.get("response", {}),
            endpoints.get("TourListrate", {}).get("response"),
        ),
        "VisaList": lambda r: audit_visa(r.get("response", {})),
        "RestaurantList": lambda r: audit_restaurant_list(r.get("response", {})),
        "TransferList": lambda r: audit_transfer(r.get("response", {})),
        "PackageList": lambda r: audit_package_list(r.get("response", {})),
    }

    report_endpoints: dict[str, dict] = {}
    severity_counter: Counter[str] = Counter()

    for name, auditor in audit_map.items():
        record = endpoints.get(name)
        if not record:
            continue
        if not record.get("ok"):
            report_endpoints[name] = {
                "status": "REQUEST_FAILED",
                "http_status": record.get("status_code"),
                "findings": [],
            }
            continue
        findings = auditor(record)
        for f in findings:
            severity_counter[f["severity"]] += 1
        report_endpoints[name] = {
            "status": "OK",
            "http_status": record.get("status_code"),
            "latency_ms": record.get("latency_ms"),
            "findings": findings,
        }

    report = {
        "_meta": {
            "report_generated_at": datetime.now(UTC).isoformat(),
            "api_samples_path": str(samples_path),
            "samples_generated_at": samples.get("_meta", {}).get("generated_at"),
            "base_url": samples.get("_meta", {}).get("base_url"),
        },
        "summary": {
            "endpoints_audited": len(report_endpoints),
            "total_findings": sum(severity_counter.values()),
            "by_severity": dict(severity_counter),
        },
        "endpoints": report_endpoints,
    }

    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    # Console summary
    print()
    print(c("━" * 70, "36"))
    print(c(f"  AUDIT REPORT — {len(report_endpoints)} endpoints", "36"))
    print(c("━" * 70, "36"))
    print()
    for name, ep in report_endpoints.items():
        if ep["status"] == "REQUEST_FAILED":
            print(c(f"  ✗ {name:<20} REQUEST FAILED", "31"))
            continue
        sb = sum(1 for f in ep["findings"] if f["severity"] == "SUPPLIER_BUG")
        pi = sum(1 for f in ep["findings"] if f["severity"] == "PARSER_ISSUE")
        info = sum(1 for f in ep["findings"] if f["severity"] == "INFO")
        parts = []
        if sb:
            parts.append(c(f"{sb} supplier bug{'s' if sb != 1 else ''}", "31"))
        if pi:
            parts.append(c(f"{pi} parser issue{'s' if pi != 1 else ''}", "33"))
        if info:
            parts.append(c(f"{info} info", "90"))
        print(f"  {name:<20} {' · '.join(parts) if parts else c('clean', '32')}")

    print()
    print(c(f"  Saved → {report_path}", "36"))
    print(c("  Share that file when reporting issues.", "36"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())