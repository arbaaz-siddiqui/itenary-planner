from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from langchain_core.tools import tool

from booking_api import call_visa_info
from booking_api.endpoints import VISA_INDIAN_NATIONALITY_ID
from core import TripPlannerError
from mcp_tools.server import mcp
from parsers import parse_visa_response
from reference_data_loader import resolve_country_id

logger = logging.getLogger(__name__)

# Names customers use for a country, as the supplier's 2-letter code.
_COUNTRY_ALIASES = {
    "uae": "AE", "emirates": "AE", "the uae": "AE", "dubai": "AE", "abu dhabi": "AE",
    "uk": "GB", "britain": "GB", "great britain": "GB", "england": "GB", "london": "GB",
    "usa": "US", "us": "US", "america": "US", "united states": "US",
    "viet nam": "VN",
}
# Short names for the wording ("UAE weekends", "UAE immigration").
_SHORT_NAMES = {"AE": "UAE", "GB": "UK", "US": "USA"}
# Countries whose working week skips Friday and Saturday; the rest Sat and Sun.
_FRI_SAT_WEEKEND = {"EG", "OM", "SA", "QA", "KW", "BH", "JO"}
_VISA_TABLE: dict[str, Any] = {"at": 0.0, "rows": []}
_VISA_TABLE_TTL = 6 * 3600


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split())


def visa_countries() -> list[dict[str, Any]]:
    """The supplier's visa country table (GET /api/visa/v1/countries), cached
    for 6 hours. [] when it cannot be read (the failure is not cached)."""
    import time

    from reference_data_loader import supplier_lookup_enabled

    if not supplier_lookup_enabled():
        return []
    if _VISA_TABLE["rows"] and time.monotonic() - _VISA_TABLE["at"] < _VISA_TABLE_TTL:
        return list(_VISA_TABLE["rows"])
    try:
        from booking_api import call_visa_countries

        rows = [r for r in (call_visa_countries() or {}).get("result") or [] if isinstance(r, dict)]
    except Exception as e:  # noqa: BLE001 - the offline fallback answers instead
        logger.warning("visa country table failed: %s", e)
        return []
    if rows:
        _VISA_TABLE.update(at=time.monotonic(), rows=rows)
    return rows


def country_label(row: dict[str, Any]) -> str:
    """The short name used in wording: "UAE", "Singapore"."""
    code = str(row.get("countryCode") or "").upper()
    return _SHORT_NAMES.get(code) or str(row.get("countryName") or code).strip()


def resolve_visa_country(destination: str) -> tuple[dict[str, Any] | None, str]:
    """(the supplier's visa row for a destination, the country name asked about).

    The destination may be a country ("Singapore", "UAE", "AE") or a city
    ("Dubai", "Bangkok"): a city counts through its country. The row is None
    when the supplier sells no visa for that country; the name is "" when the
    destination could not be placed at all.
    """
    want = _plain(destination)
    if not want:
        return None, ""
    rows = visa_countries()
    if not rows:
        # Supplier table unreadable: the curated ids still cover Dubai/UAE.
        cid = resolve_country_id(destination) or resolve_country_id(destination.strip().upper())
        if cid is None:
            from reference_data_loader import resolve_city

            city = resolve_city(destination, supplier=False)
            cid = city.get("country_id") if city else None
        if cid == 213:
            return {"countryId": 213, "countryName": "United Arab Emirates", "countryCode": "AE"}, "UAE"
        return (None, destination.strip()) if cid else (None, "")
    code = _COUNTRY_ALIASES.get(want, want.upper() if len(want) == 2 else "")
    for r in rows:
        names = {_plain(r.get("countryName")), _plain(r.get("countryNameDefault"))}
        if want in names or (code and str(r.get("countryCode") or "").upper() == code):
            return r, country_label(r)
    from reference_data_loader import resolve_city

    city = resolve_city(destination)
    if city is None:
        # Not a city we know: most likely a country the supplier sells no
        # visa for ("Japan").
        return None, destination.strip()
    for r in rows:
        if city.get("country_id") and r.get("countryId") == city.get("country_id"):
            return r, country_label(r)
        if _plain(r.get("countryName")) == _plain(city.get("country_name")):
            return r, country_label(r)
    return None, str(city.get("country_name") or destination).strip()

# Spellings customers/LLMs actually use for an Indian passport.
_INDIAN_NATIONALITY_ALIASES = {
    "india",
    "indian",
    "in",
    "ind",
    "republic of india",
    "indian passport",
}


def _impl(
    destination_country: str,
    nationality_country: str,
    travel_date: str,
    adults: int = 1,
    children: int = 0,
) -> dict[str, Any]:
    """Get visa requirements and (where available) pricing."""
    try:
        # The supplier's own visa country table decides (its ids differ from
        # cities.json: Singapore is 115 there, 191 here).
        row, country = resolve_visa_country(destination_country)
        if row is None:
            sold = ", ".join(country_label(r) for r in visa_countries())
            if not country:
                return {
                    "error": True,
                    "error_type": "UnsupportedRoute",
                    "message": f"Unknown destination country: {destination_country!r}",
                    "agent_instructions": "Ask the customer which country they are travelling to.",
                }
            if not sold:
                # The supplier's list could not be read: not a "no".
                return {
                    "error": True,
                    "error_type": "VisaInfoFailed",
                    "message": f"Could not check {country} visas with the supplier just now.",
                    "agent_instructions": (
                        "Say the visa check did not go through and offer to try again; "
                        "never recite visa rules from memory."
                    ),
                }
            return {
                "error": True,
                "error_type": "UnsupportedRoute",
                "message": f"We do not sell visas for {country}.",
                "agent_instructions": (
                    f"Say plainly that we do not arrange {country} visas"
                    + (f" (we sell visas for {sold})" if sold else "")
                    + ". Never recite visa rules from memory and never imply a "
                    "technical problem."
                ),
            }
        country_id = int(row["countryId"])
        # The visa service has its own country table, so we must NOT reuse
        # resolve_country_id() here (it returns 105 for India, which makes the
        # supplier return unpriced fares). Indian passport is the only
        # nationality we sell to; anything else we can't price.
        nationality = (nationality_country or "India").strip().lower()
        if nationality not in _INDIAN_NATIONALITY_ALIASES:
            return {
                "error": True,
                "message": (
                    f"Visa pricing is only available for Indian passports; "
                    f"got {nationality_country!r}."
                ),
                "error_type": "UnsupportedRoute",
            }
        raw = call_visa_info(
            country_id=country_id,
            nationality_id=VISA_INDIAN_NATIONALITY_ID,
            citizen_id=VISA_INDIAN_NATIONALITY_ID,
            travel_date=travel_date,
            adults=adults,
            children=children,
        )
        options = parse_visa_response(raw)
        option_dicts = []
        policy = options[0] if options else None
        for o in options:
            d = o.model_dump()
            # Presentation-ready extras so the agent doesn't have to derive them
            # (and can't invent them): never "0 days", and a flat checklist.
            d["processing_display"] = o.processing_display
            d["price_display"] = o.price_display
            d["document_checklist"] = [doc.name for doc in o.checklist_documents]
            # Full fare matrix: Normal/Express x Adult/Child, already formatted
            # so the agent relays it rather than deriving (or inventing) it.
            d["child_price_display"] = o.child_price_display
            d["fare_lines"] = o.fare_lines
            # What the whole party pays, per processing track, so the reply can
            # quote it instead of multiplying (asked for "2 adults and 1 child",
            # it gave only the adult fare). Uses each pax type's own fare.
            d["party_totals"] = _party_totals(o, adults, children)
            if d["party_totals"]:
                d["party_total_display"] = d["party_totals"][0]["display"]
            # Refund answer, verbatim from the published policy.
            d["refund_display"] = o.refund_policy or (
                "Non-refundable." if o.refundable is False else ""
            )
            option_dicts.append(d)
        # What this conversation was quoted, so a plan can only carry a visa
        # price that was offered (see quote_registry).
        from mcp_tools.quote_registry import remember

        remember("visa", [d.get("price_per_person_inr") for d in option_dicts])
        from mcp_tools.quote_registry import remember_label

        for d in option_dicts:
            remember_label("visa", d.get("price_per_person_inr"), str(d.get("visa_type") or ""))
        return {
            "options": option_dicts,
            "total_results": len(options),
            "pricing_available": any(o.pricing_available for o in options),
            "pricing_note": (
                "The supplier returned no fares for these visas — quote 'On "
                "Request' and confirm with the supplier. Do NOT quote ₹0."
                if not any(o.pricing_available for o in options)
                else ""
            ),
            # Same for every option of this visa type: whether the visa can be
            # ready before the flight, worked out from the published
            # processing times, the country's weekend and holidays. The agent relays
            # `timeline.note`; it never promises a date of its own.
            "timeline": visa_timeline(
                travel_date,
                country=country,
                country_code=str(row.get("countryCode") or ""),
                track_times=policy.track_times if policy else {},
                weekend_days=policy.weekend_days if policy else [],
                holidays=policy.holidays if policy else [],
                apply_before_text=policy.apply_before_text if policy else "",
                express_note=policy.express_note if policy else "",
            ),
            "refund_policy": (policy.refund_policy if policy else ""),
            "refundable": (policy.refundable if policy else None),
            "country": country,
            "search_params": {
                "destination": destination_country,
                "country_id": country_id,
                "nationality": nationality_country,
                "travel_date": travel_date,
                "adults": adults,
                "children": children,
            },
        }
    except TripPlannerError as e:
        logger.warning("get_visa_info error: %s", e)
        return {"error": True, "message": e.message, **e.to_dict()}
    except Exception as e:
        logger.exception("get_visa_info unexpected error")
        return {"error": True, "message": str(e), "error_type": type(e).__name__}


def _party_totals(option: Any, adults: int, children: int) -> list[dict[str, Any]]:
    """[{track, total_inr, display}] for this party, one per priced track.

    Nothing when a fare the party needs is missing (a child with no child
    fare): a partial total quoted as the whole would be wrong.
    """
    from core import format_inr

    party = " + ".join(
        x for x in (
            f"{adults} adult{'s' if adults != 1 else ''}" if adults else "",
            f"{children} child{'ren' if children != 1 else ''}" if children else "",
        ) if x
    )
    by_track: dict[str, dict[str, float]] = {}
    for f in option.fares:
        if f.price_inr > 0:
            by_track.setdefault(f.process_type or "Normal", {})[f.pax_type.lower()[:5]] = f.price_inr
    out = []
    for track, fares in by_track.items():
        if (adults and "adult" not in fares) or (children and "child" not in fares):
            continue
        total = round(adults * fares.get("adult", 0) + children * fares.get("child", 0), 2)
        out.append({"track": track, "total_inr": total,
                    "display": f"{format_inr(total)} for {party} ({track})"})
    return out


_IST = timezone(timedelta(hours=5, minutes=30))
_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _max_working_days(text: str) -> int | None:
    """Upper bound of a published processing time, in working days.

    "2–4 Working Days" -> 4; "24–48 Hours" -> 2; "1 Working Day" -> 1.
    The upper bound, because a customer told "it fits" must not be let down by
    the slow end of the range.
    """
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    if not nums:
        return None
    top = max(nums)
    if re.search(r"hour", text, re.I):
        return max(1, math.ceil(top / 24))
    return top


def _working_days_between(start: date, end: date, weekend: set[int], holidays: set[date]) -> int:
    """Working days strictly after `start` and strictly before `end`.

    Today is not counted (an application made today is verified first and
    submitted on a later working day), nor is the travel day itself.
    """
    n = 0
    d = start + timedelta(days=1)
    while d < end:
        if d.weekday() not in weekend and d not in holidays:
            n += 1
        d += timedelta(days=1)
    return n


def visa_timeline(
    travel_date: str,
    *,
    track_times: dict[str, str],
    weekend_days: list[str],
    holidays: list[str],
    apply_before_text: str = "",
    express_note: str = "",
    today: date | None = None,
    country: str = "UAE",
    country_code: str = "",
) -> dict[str, Any] | None:
    """Can the visa be ready before the flight? None when it cannot be judged.

    Uses only published facts: each track's processing time, the destination
    country's weekend (the supplier's when it gives one), the supplier's
    holiday list and its "apply at least N days before" advice. Approval
    itself is the country's immigration's decision, so the note never promises
    a visa, only whether processing time fits.
    """
    try:
        travel = datetime.strptime(str(travel_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = today or datetime.now(_IST).date()
    if travel <= today or not track_times:
        return None
    default_weekend = {4, 5} if country_code.upper() in _FRI_SAT_WEEKEND else {5, 6}
    weekend = {_DAY_INDEX[d[:3].lower()] for d in weekend_days if d[:3].lower() in _DAY_INDEX} or default_weekend
    hols: set[date] = set()
    for h in holidays:
        try:
            hols.add(datetime.strptime(str(h)[:10], "%Y-%m-%d").date())
        except ValueError:
            continue
    calendar_days = (travel - today).days
    working = _working_days_between(today, travel, weekend, hols)

    tracks = []
    for tier, text in track_times.items():
        need = _max_working_days(text)
        tracks.append({"track": tier, "takes": text, "working_days_needed": need,
                       "fits": need is not None and working >= need})
    by = {t["track"]: t for t in tracks}
    normal_fits = bool(by.get("Normal", {}).get("fits"))
    express_fits = bool(by.get("Express", {}).get("fits"))
    recommended = "Normal" if normal_fits else ("Express" if express_fits else "")

    lead = (f"Travel is {calendar_days} day{'s' if calendar_days != 1 else ''} away, "
            f"which leaves {working} working day{'s' if working != 1 else ''} for processing "
            f"({country} weekends{' and holidays' if hols else ''} excluded).")
    parts = [lead]
    normal_txt = by.get("Normal", {}).get("takes")
    express_txt = by.get("Express", {}).get("takes")
    if normal_fits:
        parts.append(f"Normal processing ({normal_txt}) fits.")
    elif express_fits:
        parts.append(
            f"Normal processing ({normal_txt}) may not finish in time. "
            f"Express ({express_txt}) fits"
            + (f"; {express_note[0].lower() + express_note[1:]}" if express_note else ".")
        )
    else:
        parts.append(
            "Neither Normal" + (f" ({normal_txt})" if normal_txt else "")
            + (f" nor Express ({express_txt})" if express_txt else "")
            + " is sure to finish before departure. Apply today and speak to the booking team."
        )
    m = re.search(r"(\d+)", apply_before_text or "")
    if apply_before_text and m and calendar_days < int(m.group(1)):
        parts.append(f"This is later than the published advice: {apply_before_text}")
    parts.append(f"Approval is decided by {country} immigration and cannot be guaranteed.")
    return {
        "calendar_days": calendar_days,
        "working_days": working,
        "tracks": tracks,
        "recommended_track": recommended,
        "apply_before": apply_before_text,
        "note": " ".join(parts),
    }


from mcp_tools.result_cache import cache_impl
get_visa_info_tool = tool(cache_impl("get_visa_info")(_impl))
get_visa_info_tool.name = "get_visa_info"
mcp.tool(name="get_visa_info")(_impl)
