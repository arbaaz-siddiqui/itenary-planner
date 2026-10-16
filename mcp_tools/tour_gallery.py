"""A tour's full photo gallery, fetched when a customer opens its card.

Tour search returns one photo per tour, so the card and its detail panel had
one picture although the website shows 6 to 15. The gallery comes from
/api/tours/medias (booking_api.endpoints.call_tour_medias), the call the
website's own gallery makes, and is fetched on demand, never during a search: one page per tour, and nobody opens
most of the cards. Photos are not per-customer data, so a process-wide cache
keyed by tour id is safe.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

log = logging.getLogger(__name__)

GALLERY_TTL_SECS = 6 * 3600.0
_MAX_TOURS = 500
_LOCK = threading.Lock()
_CACHE: OrderedDict[int, tuple[float, list[str]]] = OrderedDict()


def tour_image_urls(tour_id: int) -> list[str]:
    """The tour's photos as CDN URLs, cover first. Empty if none can be read."""
    tour_id = int(tour_id)
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(tour_id)
        if hit and now - hit[0] < GALLERY_TTL_SECS:
            return list(hit[1])

    from booking_api.endpoints import call_tour_medias
    from parsers import parse_tour_medias

    try:
        urls = parse_tour_medias(call_tour_medias(tour_id=tour_id))
    except Exception as e:  # noqa: BLE001 -- photos are a bonus, never an error
        log.warning("tour_gallery_failed tour=%s: %s", tour_id, e)
        return []
    with _LOCK:
        _CACHE[tour_id] = (now, urls)
        _CACHE.move_to_end(tour_id)
        while len(_CACHE) > _MAX_TOURS:
            _CACHE.popitem(last=False)
    return list(urls)
