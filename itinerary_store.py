"""itinerary_store — build, persist, and retrieve generated itinerary PDFs.

Single source of truth shared by every surface:
  - Streamlit builds a PDF and offers it for download.
  - The agent tool builds one and returns its id + public URL.
  - The WhatsApp/FastAPI service serves a saved PDF back by id (for Twilio media).

PDFs are written to `StateSettings.itinerary_dir` (a mounted volume in prod) so
they survive restarts and can be fetched by id from any worker.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from itinerary_pdf import ItineraryDoc, build_itinerary_pdf, itinerary_doc_from_dict
from settings import get_state_settings


def _dir() -> Path:
    d = Path(get_state_settings().itinerary_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_id(data: dict) -> str:
    """Stable-ish short id from the content (so identical content reuses a file)."""
    blob = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]  # id only, not security


def save_itinerary_pdf(doc: ItineraryDoc | dict) -> tuple[str, Path]:
    """Render and persist a PDF. Returns (itinerary_id, file_path)."""
    if isinstance(doc, ItineraryDoc):
        from dataclasses import asdict

        data = asdict(doc)
        model = doc
    else:
        data = dict(doc)
        model = itinerary_doc_from_dict(data)

    itinerary_id = _make_id(data)
    path = _dir() / f"{itinerary_id}.pdf"
    if not path.exists():
        path.write_bytes(build_itinerary_pdf(model))
    return itinerary_id, path


def get_itinerary_path(itinerary_id: str) -> Path | None:
    """Resolve a saved PDF by id, or None. Guards against path traversal."""
    if not itinerary_id or not itinerary_id.isalnum():
        return None
    path = _dir() / f"{itinerary_id}.pdf"
    return path if path.exists() else None


def public_url_for(itinerary_id: str) -> str | None:
    """Public https URL for a saved PDF, if PUBLIC_BASE_URL is configured."""
    base = get_state_settings().public_base_url.rstrip("/")
    if not base:
        return None
    return f"{base}/itinerary/{itinerary_id}.pdf"
