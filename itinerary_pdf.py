"""itinerary_pdf — branded itinerary PDF generation.

Renders a customer-facing trip itinerary onto the Gujju Tours letterhead: the
header banner (`assets/letterhead_header.jpg`) and footer banner
(`assets/letterhead_footer.jpg`) appear on EVERY page, with structured trip
content in between.

The content model (`ItineraryDoc` and its parts) is plain data so callers — the
Streamlit button, the WhatsApp flow, the agent tool — all build the same shape.
Numbers come from the caller (the agent's recorded selections / tool data), not
from free-form model text, so the PDF can't invent prices.

Public API:
    build_itinerary_pdf(doc) -> bytes        # the rendered PDF
    itinerary_doc_from_dict(data) -> ItineraryDoc
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from core import format_inr

# --- Brand ---
_ASSETS = Path(__file__).resolve().parent / "assets"
HEADER_IMG = _ASSETS / "letterhead_header.jpg"
FOOTER_IMG = _ASSETS / "letterhead_footer.jpg"

BRAND_RED = (238, 74, 52)  # Gujju Tours accent (from the footer banner)
INK = (33, 37, 41)
MUTED = (110, 116, 124)
RULE = (222, 226, 230)

# Letterhead banners are ~12:1; on an A4 (210mm wide) full-bleed band they are
# about 17mm tall. We reserve margins so body text never collides with them.
_BANNER_H = 17.0
_TOP_MARGIN = _BANNER_H + 6
_BOTTOM_MARGIN = _BANNER_H + 6


# =============================================================================
# Content model
# =============================================================================
@dataclass
class LineItem:
    """One priced row (flight, hotel, tour, transfer, restaurant, visa)."""

    label: str
    detail: str = ""
    amount_inr: float | None = None  # None -> "On Request"


@dataclass
class DayPlan:
    """One day of the trip."""

    title: str  # e.g. "Day 1 — Arrival & Marina"
    items: list[str] = field(default_factory=list)


@dataclass
class PaymentInstallment:
    label: str
    amount_inr: float
    due_date_iso: str = ""


@dataclass
class ItineraryDoc:
    """Everything needed to render a clear, customer-facing itinerary PDF."""

    # Header block
    customer_name: str = ""
    destination: str = "Dubai"
    origin_city: str = ""
    start_date: str = ""  # human or ISO; rendered verbatim
    end_date: str = ""
    nights: int | None = None
    party_summary: str = ""  # e.g. "2 adults, 1 child (age 7)"
    reference: str = ""  # quote/booking ref

    # Body
    overview: str = ""  # short intro paragraph
    day_plans: list[DayPlan] = field(default_factory=list)
    components: list[LineItem] = field(default_factory=list)  # selected services
    inclusions: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)

    # Money
    total_inr: float | None = None
    payment_schedule: list[PaymentInstallment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # visa/cancellation/compliance


def itinerary_doc_from_dict(data: dict[str, Any]) -> ItineraryDoc:
    """Build an ItineraryDoc from a plain dict (e.g. an agent tool's args).

    Tolerant: unknown keys ignored, missing keys default, nested lists coerced.
    """

    def _items(key: str) -> list[str]:
        v = data.get(key) or []
        return [str(x) for x in v] if isinstance(v, list) else []

    day_plans = []
    for d in data.get("day_plans") or []:
        if isinstance(d, dict):
            day_plans.append(
                DayPlan(
                    title=str(d.get("title") or ""),
                    items=[str(x) for x in (d.get("items") or [])],
                )
            )

    components = []
    for c in data.get("components") or []:
        if isinstance(c, dict):
            amt = c.get("amount_inr")
            components.append(
                LineItem(
                    label=str(c.get("label") or ""),
                    detail=str(c.get("detail") or ""),
                    amount_inr=float(amt) if isinstance(amt, (int, float)) else None,
                )
            )

    schedule = []
    for s in data.get("payment_schedule") or []:
        if isinstance(s, dict) and isinstance(s.get("amount_inr"), (int, float)):
            schedule.append(
                PaymentInstallment(
                    label=str(s.get("label") or ""),
                    amount_inr=float(s["amount_inr"]),
                    due_date_iso=str(s.get("due_date_iso") or ""),
                )
            )

    total = data.get("total_inr")
    nights = data.get("nights")
    return ItineraryDoc(
        customer_name=str(data.get("customer_name") or ""),
        destination=str(data.get("destination") or "Dubai"),
        origin_city=str(data.get("origin_city") or ""),
        start_date=str(data.get("start_date") or ""),
        end_date=str(data.get("end_date") or ""),
        nights=int(nights) if isinstance(nights, (int, float)) else None,
        party_summary=str(data.get("party_summary") or ""),
        reference=str(data.get("reference") or ""),
        overview=str(data.get("overview") or ""),
        day_plans=day_plans,
        components=components,
        inclusions=_items("inclusions"),
        exclusions=_items("exclusions"),
        total_inr=float(total) if isinstance(total, (int, float)) else None,
        payment_schedule=schedule,
        notes=_items("notes"),
    )


# =============================================================================
# PDF
# =============================================================================
class _ItineraryPDF(FPDF):
    """A4 PDF that stamps the letterhead banners on every page."""

    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=_BOTTOM_MARGIN)
        self.set_margins(left=14, top=_TOP_MARGIN, right=14)

    def header(self) -> None:
        if HEADER_IMG.exists():
            self.image(str(HEADER_IMG), x=0, y=0, w=self.w, h=_BANNER_H)
        self.set_y(_TOP_MARGIN)

    def footer(self) -> None:
        if FOOTER_IMG.exists():
            self.image(str(FOOTER_IMG), x=0, y=self.h - _BANNER_H, w=self.w, h=_BANNER_H)


def _money(amount: float | None) -> str:
    return format_inr(amount) if isinstance(amount, (int, float)) else "On Request"


def _section_title(pdf: _ItineraryPDF, text: str) -> None:
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*BRAND_RED)
    pdf.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(2)
    pdf.set_text_color(*INK)


def _kv(pdf: _ItineraryPDF, key: str, value: str) -> None:
    if not value:
        return
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(38, 6, key)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _bullets(pdf: _ItineraryPDF, items: list[str]) -> None:
    pdf.set_font("Helvetica", "", 10)
    for it in items:
        pdf.set_text_color(*BRAND_RED)
        pdf.cell(5, 6, chr(149))  # bullet
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 6, _ascii(it), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _ascii(text: str) -> str:
    """Fold to latin-1 (the core PDF fonts' charset) so text never raises.

    Common typographic unicode (dashes, quotes, arrows, bullets, the rupee sign)
    is mapped to plain ASCII; the rupee glyph becomes 'Rs ' (amounts are
    formatted with format_inr, which uses the rupee sign).
    """
    repl = {
        "₹": "Rs ",   # rupee
        "–": "-",      # en dash
        "—": "-",      # em dash
        "‘": "'",      # left single quote
        "’": "'",      # right single quote
        "“": '"',      # left double quote
        "”": '"',      # right double quote
        "→": "->",     # right arrow
        "←": "<-",     # left arrow
        "•": "-",      # bullet
        "·": "-",      # middle dot
        "…": "...",    # ellipsis
        " ": " ",      # non-breaking space
    }
    for bad, good in repl.items():
        text = text.replace(bad, good)
    # Common typographic unicode is handled above; anything still unmappable is
    # encoded with 'replace' (a rare '?' is acceptable and won't raise).
    return text.encode("latin-1", "replace").decode("latin-1")


def build_itinerary_pdf(doc: ItineraryDoc) -> bytes:
    """Render the itinerary onto the branded letterhead and return PDF bytes."""
    pdf = _ItineraryPDF()
    pdf.set_title(f"{doc.destination} Itinerary")
    pdf.add_page()

    # Title block
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*INK)
    pdf.cell(0, 9, _ascii(f"{doc.destination} Trip Itinerary"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if doc.customer_name:
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6, _ascii(f"Prepared for {doc.customer_name}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*INK)
    pdf.ln(2)

    # Trip-at-a-glance
    _section_title(pdf, "Trip Summary")
    route = (doc.origin_city and f"{doc.origin_city} -> {doc.destination}") or doc.destination
    _kv(pdf, "Route", _ascii(route))
    dates = " to ".join(d for d in (doc.start_date, doc.end_date) if d)
    if doc.nights:
        dates = f"{dates}  ({doc.nights} nights)" if dates else f"{doc.nights} nights"
    _kv(pdf, "Travel dates", _ascii(dates))
    _kv(pdf, "Travellers", _ascii(doc.party_summary))
    _kv(pdf, "Reference", _ascii(doc.reference))

    if doc.overview:
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, _ascii(doc.overview), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Day-by-day
    if doc.day_plans:
        _section_title(pdf, "Day-by-Day Plan")
        for day in doc.day_plans:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*INK)
            pdf.multi_cell(0, 6, _ascii(day.title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            _bullets(pdf, day.items)
            pdf.ln(1)

    # Selected services + pricing table
    if doc.components:
        _section_title(pdf, "Your Trip Includes")
        _component_table(pdf, doc.components)

    if doc.total_inr is not None:
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*BRAND_RED)
        pdf.cell(0, 8, _ascii(f"Total (all-inclusive): {_money(doc.total_inr)}"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*INK)

    # Payment schedule
    if doc.payment_schedule:
        _section_title(pdf, "Payment Schedule")
        for inst in doc.payment_schedule:
            line = f"{inst.label}: {_money(inst.amount_inr)}"
            if inst.due_date_iso:
                line += f"  (due {inst.due_date_iso})"
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, _ascii(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Inclusions / exclusions
    if doc.inclusions:
        _section_title(pdf, "Inclusions")
        _bullets(pdf, doc.inclusions)
    if doc.exclusions:
        _section_title(pdf, "Exclusions")
        _bullets(pdf, doc.exclusions)

    # Notes
    if doc.notes:
        _section_title(pdf, "Important Notes")
        _bullets(pdf, doc.notes)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*MUTED)
        pdf.ln(1)
        pdf.multi_cell(0, 5, "All taxes and fees included unless stated otherwise. "
                       "Prices are valid as quoted and subject to availability at booking.",
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*INK)

    out = pdf.output()
    return bytes(out)


def _component_table(pdf: _ItineraryPDF, components: list[LineItem]) -> None:
    col_amt = 32
    col_main = pdf.w - pdf.l_margin - pdf.r_margin - col_amt
    # header row
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(245, 246, 248)
    pdf.set_text_color(*MUTED)
    pdf.cell(col_main, 7, "  Service", border=0, fill=True)
    pdf.cell(col_amt, 7, "Amount  ", border=0, align="R", fill=True,
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*INK)
    for c in components:
        pdf.set_font("Helvetica", "B", 10)
        top = pdf.get_y()
        pdf.multi_cell(col_main, 6, "  " + _ascii(c.label), new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(col_amt, 6, _ascii(_money(c.amount_inr)) + "  ", align="R",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if c.detail:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*MUTED)
            pdf.multi_cell(0, 5, "  " + _ascii(c.detail), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(*INK)
        pdf.set_draw_color(*RULE)
        y = pdf.get_y() + 0.5
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(1.5)
        _ = top
