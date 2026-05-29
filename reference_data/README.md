# reference_data/

Static lookup data loaded once at startup by `reference_data_loader.py`.

| File | What it does |
|---|---|
| `cities.json` | Indian-city → IATA mapping used to resolve `origin_city` to an airport code before calling the flight API. |
| `hotels/dubai.json` | Dubai hotel master list (hotel-id → name + area). Used by the hotel parser to resolve numeric IDs to display names. Only IDs 206 and 509 are confirmed real on the staging API; the rest are placeholders. |
