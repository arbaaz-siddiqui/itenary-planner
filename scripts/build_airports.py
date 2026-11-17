"""Build reference_data/airports.json from OurAirports public data.

Keeps large and medium airports with scheduled passenger service and an IATA
code: the airports a customer can actually fly into. For every city the
airports are listed main first (an explicit choice for multi-airport cities
below, then large before medium), so `reference_data_loader.resolve_iata`
can turn "London" into LHR and "Bangkok" into BKK.

    python -m scripts.build_airports                      # downloads the CSVs
    python -m scripts.build_airports --airports a.csv --countries c.csv

Source: https://ourairports.com/data/ (public domain). Re-run to refresh.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import urllib.request
from pathlib import Path

BASE = "https://davidmegginson.github.io/ourairports-data"
OUT = Path(__file__).resolve().parent.parent / "reference_data" / "airports.json"
_SEP = (",", ":")
KEEP_TYPES = {"large_airport": 2, "medium_airport": 1}

# The airport most travellers mean by the city, where a city has several.
PREFERRED = {
    "london": "LHR", "paris": "CDG", "new york": "JFK", "tokyo": "HND", "osaka": "KIX",
    "bangkok": "BKK", "dubai": "DXB", "milan": "MXP", "rome": "FCO", "moscow": "SVO",
    "istanbul": "IST", "seoul": "ICN", "shanghai": "PVG", "beijing": "PEK",
    "kuala lumpur": "KUL", "jakarta": "CGK", "sao paulo": "GRU", "washington": "IAD",
    "chicago": "ORD", "toronto": "YYZ", "montreal": "YUL", "houston": "IAH",
    "taipei": "TPE", "buenos aires": "EZE", "rio de janeiro": "GIG", "berlin": "BER",
    "stockholm": "ARN", "oslo": "OSL", "abu dhabi": "AUH", "sharjah": "SHJ",
    "mumbai": "BOM", "delhi": "DEL", "new delhi": "DEL", "colombo": "CMB",
    "ho chi minh city": "SGN", "hanoi": "HAN", "manila": "MNL", "dhaka": "DAC",
}

# Names customers use for a place whose airport sits in another municipality.
ALIASES = {
    "bali": "DPS", "maldives": "MLE", "male": "MLE", "phuket": "HKT", "krabi": "KBV",
    "langkawi": "LGK", "mauritius": "MRU", "seychelles": "SEZ", "zanzibar": "ZNZ",
    "goa": "GOI", "hong kong": "HKG", "macau": "MFM", "singapore": "SIN",
    "ho chi minh": "SGN", "saigon": "SGN", "bombay": "BOM", "calcutta": "CCU",
    "madras": "MAA", "bengaluru": "BLR", "bangalore": "BLR", "new york city": "JFK",
    "nyc": "JFK", "la": "LAX", "los angeles": "LAX", "san francisco": "SFO",
    "venice": "VCE", "florence": "FLR", "prague": "PRG", "vienna": "VIE",
    "zurich": "ZRH", "geneva": "GVA", "amsterdam": "AMS", "barcelona": "BCN",
    "madrid": "MAD", "lisbon": "LIS", "athens": "ATH", "cairo": "CAI",
    "muscat": "MCT", "doha": "DOH", "riyadh": "RUH", "jeddah": "JED",
    "baku": "GYD", "tbilisi": "TBS", "almaty": "ALA", "tashkent": "TAS",
    "sydney": "SYD", "melbourne": "MEL", "auckland": "AKL", "cape town": "CPT",
    "johannesburg": "JNB", "nairobi": "NBO", "kathmandu": "KTM", "thimphu": "PBH",
    "pattaya": "UTP", "chiang mai": "CNX", "siem reap": "SAI", "kochi": "COK",
    "cochin": "COK",
}


def _read(path: str | None, name: str) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    with urllib.request.urlopen(f"{BASE}/{name}", timeout=60) as resp:
        return resp.read().decode("utf-8")


def _city_keys(municipality: str) -> list[str]:
    """"Paris (Roissy-en-France, Val-d'Oise)" -> ["paris"]; "Kuta, Badung" ->
    ["kuta", "badung"]."""
    text = re.sub(r"\(.*?\)", "", municipality or "")
    keys = []
    for part in re.split(r"[,/]", text):
        k = " ".join(part.strip().lower().split())
        if k and k not in keys:
            keys.append(k)
    return keys


def build(airports_csv: str, countries_csv: str) -> dict:
    countries = {r["code"]: r["name"] for r in csv.DictReader(io.StringIO(countries_csv))}
    airports: dict[str, dict] = {}
    cities: dict[str, list[tuple[int, str]]] = {}
    for r in csv.DictReader(io.StringIO(airports_csv)):
        size = KEEP_TYPES.get(r["type"])
        code = (r.get("iata_code") or "").strip().upper()
        if not size or r.get("scheduled_service") != "yes" or not re.fullmatch(r"[A-Z]{3}", code):
            continue
        keys = _city_keys(r.get("municipality") or "")
        # Short keys keep the file under the 500 KB pre-commit limit:
        # n name, c city, cc country code, s size (2 large, 1 medium).
        airports[code] = {
            "n": r["name"],
            "c": (r.get("municipality") or "").split("(")[0].split(",")[0].strip(),
            "cc": r["iso_country"],
            "lat": round(float(r["latitude_deg"]), 4),
            "lng": round(float(r["longitude_deg"]), 4),
            "s": size,
        }
        for k in keys:
            cities.setdefault(k, []).append((size, code))
    index: dict[str, list[str]] = {}
    for key, rows in sorted(cities.items()):
        codes = [c for _s, c in sorted(rows, key=lambda t: (-t[0], t[1]))]
        pref = PREFERRED.get(key)
        if pref in codes:
            codes.remove(pref)
            codes.insert(0, pref)
        index[key] = codes
    # A preferred airport filed under another town (KUL is "Sepang") still
    # serves the city: without this "Kuala Lumpur" fell back to Subang (SZB).
    for city, code in PREFERRED.items():
        if code in airports:
            rest = [c for c in index.get(city, []) if c != code]
            index[city] = [code, *rest]
    for alias, code in ALIASES.items():
        if code in airports:
            rest = [c for c in index.get(alias, []) if c != code]
            index[alias] = [code, *rest]
    return {
        "_README": ("Generated by scripts/build_airports.py from OurAirports (public domain): "
                    "large and medium airports with scheduled service. Do not edit by hand."),
        "countries": {cc: countries.get(cc, cc) for cc in sorted({a["cc"] for a in airports.values()})},
        "airports": dict(sorted(airports.items())),
        "cities": dict(sorted(index.items())),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--airports", help="local airports.csv (default: download)")
    ap.add_argument("--countries", help="local countries.csv (default: download)")
    args = ap.parse_args()
    data = build(_read(args.airports, "airports.csv"), _read(args.countries, "countries.csv"))
    # One airport / city per line keeps the file small and its diffs readable.
    lines = ["{", f'  "_README": {json.dumps(data["_README"])},',
             f'  "countries": {json.dumps(data["countries"], ensure_ascii=False)},', '  "airports": {']
    items = list(data["airports"].items())
    for i, (code, rec) in enumerate(items):
        lines.append(f"    {json.dumps(code)}: {json.dumps(rec, ensure_ascii=False, separators=_SEP)}"
                     + ("," if i < len(items) - 1 else ""))
    lines += ["  },", '  "cities": {']
    cities = list(data["cities"].items())
    for i, (k, codes) in enumerate(cities):
        lines.append(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(codes, separators=_SEP)}"
                     + ("," if i < len(cities) - 1 else ""))
    lines += ["  }", "}", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({len(items)} airports, {len(cities)} city keys)")


if __name__ == "__main__":
    main()
