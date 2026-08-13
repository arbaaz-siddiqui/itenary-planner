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

def _coerce_day_items(raw: Any) -> list[DayItem]:
    """Normalise day-plan entries to DayItem.

    Accepts the agent's dicts ({title, start, detail, kind}) and plain strings
    (older callers / free-text lines). Unknown key spellings fall back through a
    few aliases so a slightly different shape degrades to readable text rather
    than a printed Python dict.
    """
    if not isinstance(raw, list):
        return []
    out: list[DayItem] = []
    for x in raw:
        if isinstance(x, dict):
            item = DayItem(
                title=str(x.get("title") or x.get("name") or x.get("activity") or "").strip(),
                start=str(x.get("start") or x.get("time") or x.get("start_time") or "").strip(),
                detail=str(x.get("detail") or x.get("description") or x.get("note") or "").strip(),
                kind=str(x.get("kind") or x.get("type") or x.get("category") or "").strip(),
            )
            # A dict with none of the known keys would render blank; keep its
            # text rather than losing the row entirely.
            if item.is_empty:
                item = DayItem(title=" ".join(str(v) for v in x.values() if v))
            if not item.is_empty:
                out.append(item)
        elif isinstance(x, str):
            text = x.strip()
            if text:
                out.append(DayItem(title=text))
        elif isinstance(x, (list, tuple, set)):
            # A nested collection has no sensible single-line form; stringifying
            # it would print "[]" or "['a', 'b']" into the PDF.
            continue
        elif x not in (None, 0, False):
            out.append(DayItem(title=str(x)))
    return out


def _coerce_amount(value: Any) -> float | None:
    """Best-effort money -> float. Returns None only for a genuinely absent amount.

    The caller is usually an LLM tool call, so amounts arrive in whatever shape
    the model emitted: 217366, "217366", "1,24,500", "Rs 45,000.00". Treating a
    numeric STRING as "no price" is what made every PDF row read "On Request",
    so parse those instead of dropping them.

    `bool` is rejected explicitly: it subclasses int, so True would otherwise
    render as a real price of Rs 1.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Strip currency symbols/codes, thousands separators and whitespace.
        cleaned = value.strip()
        for token in ("₹", "INR", "Rs.", "Rs", "rs"):
            cleaned = cleaned.replace(token, "")
        cleaned = cleaned.replace(",", "").replace(" ", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


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
class DayItem:
    """One scheduled entry within a day.

    The agent sends these as dicts ({title, start, detail, kind}). They used to
    be flattened with str(x), which printed the raw Python dict into the PDF:
        {'detail': 'Private transfer to hotel', 'kind': 'transfer', ...}
    Keeping the parts separate lets the day plan render as a real table.
    """

    title: str = ""
    start: str = ""      # "10:00" — blank when untimed
    detail: str = ""
    kind: str = ""       # transfer | tour | flight | hotel | meal ...

    @property
    def is_empty(self) -> bool:
        return not (self.title or self.detail)


@dataclass
class DayPlan:
    """One day of the trip."""

    title: str  # e.g. "Day 1 — Arrival & Marina"
    items: list[DayItem] = field(default_factory=list)


@dataclass
class VisaSection:
    """Visa details, carried from the earlier get_visa_info call.

    The PDF had no visa field, so visa could only ride along as a free-form
    component line — and the agent ended up re-asking the customer for visa
    details at PDF time even though it had already fetched them.
    """

    visa_type: str = ""
    entry_type: str = ""
    stay_duration: str = ""
    validity: str = ""
    processing: str = ""
    price_display: str = ""     # "On Request" when the supplier has no pricing
    documents: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.visa_type or self.documents)


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
    visa: VisaSection | None = None
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
                    items=_coerce_day_items(d.get("items")),
                )
            )

    components = []
    for c in data.get("components") or []:
        if isinstance(c, dict):
            components.append(
                LineItem(
                    label=str(c.get("label") or ""),
                    detail=str(c.get("detail") or ""),
                    amount_inr=_coerce_amount(c.get("amount_inr")),
                )
            )

    schedule = []
    for s in data.get("payment_schedule") or []:
        if not isinstance(s, dict):
            continue
        # A string amount used to fail the isinstance check and silently DROP
        # the whole installment row from the payment plan. Coerce, and skip only
        # when there is genuinely no parseable amount.
        amount = _coerce_amount(s.get("amount_inr"))
        if amount is None:
            continue
        schedule.append(
            PaymentInstallment(
                label=str(s.get("label") or ""),
                amount_inr=amount,
                due_date_iso=str(s.get("due_date_iso") or ""),
            )
        )

    visa_raw = data.get("visa")
    visa = None
    if isinstance(visa_raw, dict):
        candidate = VisaSection(
            visa_type=str(visa_raw.get("visa_type") or visa_raw.get("type") or "").strip(),
            entry_type=str(visa_raw.get("entry_type") or visa_raw.get("entry") or "").strip(),
            stay_duration=str(visa_raw.get("stay_duration") or visa_raw.get("stay") or "").strip(),
            validity=str(visa_raw.get("validity") or "").strip(),
            processing=str(
                visa_raw.get("processing")
                or visa_raw.get("processing_display")
                or ""
            ).strip(),
            price_display=str(
                visa_raw.get("price_display") or visa_raw.get("price") or ""
            ).strip(),
            documents=[str(d).strip() for d in (visa_raw.get("documents") or []) if str(d).strip()],
        )
        if not candidate.is_empty:
            visa = candidate

    total = _coerce_amount(data.get("total_inr"))
    nights_raw = _coerce_amount(data.get("nights"))
    return ItineraryDoc(
        customer_name=str(data.get("customer_name") or ""),
        destination=str(data.get("destination") or "Dubai"),
        origin_city=str(data.get("origin_city") or ""),
        start_date=str(data.get("start_date") or ""),
        end_date=str(data.get("end_date") or ""),
        nights=int(nights_raw) if nights_raw is not None else None,
        party_summary=str(data.get("party_summary") or ""),
        reference=str(data.get("reference") or ""),
        overview=str(data.get("overview") or ""),
        day_plans=day_plans,
        components=components,
        visa=visa,
        inclusions=_items("inclusions"),
        exclusions=_items("exclusions"),
        total_inr=total,
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
            _day_table(pdf, day.items)
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

    # Visa — carried from the earlier get_visa_info call, never re-asked.
    if doc.visa is not None and not doc.visa.is_empty:
        _section_title(pdf, "Visa")
        v = doc.visa
        if v.visa_type:
            _kv(pdf, "Type", _ascii(v.visa_type))
        if v.entry_type:
            _kv(pdf, "Entry", _ascii(v.entry_type))
        if v.stay_duration:
            _kv(pdf, "Stay", _ascii(v.stay_duration))
        if v.validity:
            _kv(pdf, "Validity", _ascii(v.validity))
        if v.processing:
            _kv(pdf, "Processing", _ascii(v.processing))
        if v.price_display:
            _kv(pdf, "Fee", _ascii(v.price_display))
        if v.documents:
            pdf.ln(1)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 6, "Documents required", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            _bullets(pdf, v.documents)

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


def _day_table(pdf: _ItineraryPDF, items: list[DayItem]) -> None:
    """Render one day's schedule as a Time | Activity table.

    Mirrors the "Your Trip Includes" table so the document reads as one design.
    The time column is dropped entirely when no entry that day is timed, so an
    untimed itinerary doesn't get a column of blanks.
    """
    if not items:
        return
    col_time = 20 if any(i.start for i in items) else 0
    col_main = pdf.w - pdf.l_margin - pdf.r_margin - col_time

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(245, 246, 248)
    pdf.set_text_color(*MUTED)
    if col_time:
        pdf.cell(col_time, 6, "  Time", border=0, fill=True)
    pdf.cell(col_main, 6, "  Activity", border=0, fill=True,
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*INK)

    for it in items:
        if col_time:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*MUTED)
            pdf.cell(col_time, 6, "  " + _ascii(it.start), new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.set_text_color(*INK)
        # Title carries the kind as a quiet suffix ("Arrival at DXB - transfer").
        heading = it.title or it.detail
        if it.kind and it.title:
            heading = f"{heading}  ({it.kind})"
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(col_main, 6, "  " + _ascii(heading),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if it.detail and it.title:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*MUTED)
            pdf.multi_cell(0, 5, "  " * (1 + col_time // 4) + _ascii(it.detail),
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(*INK)
        pdf.set_draw_color(*RULE)
        y = pdf.get_y() + 0.5
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(1.2)


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
