"use client";

import { useEffect, useRef, useState } from "react";
import { ExternalLink, MapPin, MessageCircle, X } from "lucide-react";
import { api } from "@/lib/api";
import type { CardRef } from "@/lib/cardActions";
import { formatInr, imagesOf, num, obj, objList, str, strList, titleOf } from "@/lib/cards";
import type { OptionItem, TourDetailSection } from "@/lib/types";
import { SafeImage } from "../SafeImage";
import { HeartButton, RatingPill } from "../cards/parts";
import { KIND_ICON, openDuration } from "../cards/Cards";

// ------------------------------------------------------------------ pieces
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-border px-5 py-4">
      <h3 className="mb-2.5 text-sm font-semibold text-fg">{title}</h3>
      {children}
    </section>
  );
}

function Facts({ rows }: { rows: [string, string | null | undefined][] }) {
  const shown = rows.filter((r): r is [string, string] => typeof r[1] === "string" && r[1].trim() !== "");
  if (!shown.length) return null;
  return (
    <dl className="grid grid-cols-[minmax(0,40%)_1fr] gap-x-4 gap-y-2 text-sm">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-muted">{k}</dt>
          <dd className="text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Bullets({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <ul className="ui-list space-y-1.5 text-sm text-fg">
      {items.map((t, i) => (
        <li key={i} className="flex gap-2">
          <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-fg-muted" aria-hidden />
          <span>{t}</span>
        </li>
      ))}
    </ul>
  );
}

function Gallery({ images, alt, kind }: { images: string[]; alt: string; kind: CardRef["kind"] }) {
  const [index, setIndex] = useState(0);
  const strip = useRef<HTMLDivElement>(null);
  const shown = images.length ? images : [""];
  return (
    <div className="relative">
      <div
        ref={strip}
        onScroll={(e) => {
          const el = e.currentTarget;
          setIndex(Math.round(el.scrollLeft / Math.max(1, el.clientWidth)));
        }}
        className="no-scrollbar flex snap-x snap-mandatory overflow-x-auto"
      >
        {shown.map((src, i) => (
          <div key={i} className="w-full shrink-0 snap-start">
            <SafeImage src={src} alt={`${alt}, photo ${i + 1}`} icon={KIND_ICON[kind]} ratio="aspect-[16/10]" eager={i === 0} />
          </div>
        ))}
      </div>
      {shown.length > 1 && (
        <div className="absolute inset-x-0 bottom-3 flex justify-center gap-1.5">
          {shown.map((_, i) => (
            <button
              key={i}
              type="button"
              aria-label={`Photo ${i + 1}`}
              onClick={() => strip.current?.scrollTo({ left: i * strip.current.clientWidth, behavior: "smooth" })}
              className={`h-1.5 rounded-full transition-all ${i === index ? "w-5 bg-white" : "w-1.5 bg-white/55"}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function mapLink(o: OptionItem): string | null {
  const lat = num(o, "latitude");
  const lng = num(o, "longitude");
  if (lat == null || lng == null) return null;
  return `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
}

function Address({ o }: { o: OptionItem }) {
  const address = str(o, "full_address");
  const link = mapLink(o);
  if (!address && !link) return null;
  return (
    <Section title="Location">
      <p className="flex items-start gap-2 text-sm text-fg">
        <MapPin size={16} className="mt-0.5 shrink-0 text-brand-red" aria-hidden />
        <span>{address}</span>
      </p>
      {link && (
        <a href={link} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-sm text-link hover:underline">
          Open in Google Maps <ExternalLink size={13} aria-hidden />
        </a>
      )}
    </Section>
  );
}

// -------------------------------------------------------------- per kind
function RestaurantBody({ o }: { o: OptionItem }) {
  const hours = objList(o, "opening_hours");
  const dishes = objList(o, "dish_photos").filter((d) => str(d, "name"));
  return (
    <>
      <Section title="About">
        <Facts
          rows={[
            ["Cuisine", strList(o, "cuisines").slice(0, 6).join(", ") || str(o, "cuisine")],
            ["Food", str(o, "veg_type")],
            ["Open", str(o, "opening_time") && str(o, "closing_time") ? `${str(o, "opening_time")} to ${str(o, "closing_time")}` : null],
            ["Open for", openDuration(str(o, "opening_time"), str(o, "closing_time"))],
            ["Seats", num(o, "seating_capacity") ? String(num(o, "seating_capacity")) : null],
            ["Menu prices", str(o, "menu_price_range_display")],
          ]}
        />
      </Section>
      {strList(o, "tags").length > 0 && (
        <Section title="Good to know">
          <ul className="ui-list flex flex-wrap gap-1.5">
            {strList(o, "tags").map((t) => (
              <li key={t} className="rounded-full border border-brand-blue/25 bg-brand-blue/10 px-2.5 py-1 text-xs text-link">
                {t}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {dishes.length > 0 && (
        <Section title="On the menu">
          <ul className="ui-list grid grid-cols-3 gap-3">
            {dishes.slice(0, 9).map((d, i) => (
              <li key={i}>
                <SafeImage src={str(d, "image_url")} alt={str(d, "name")!} ratio="aspect-square" className="rounded-xl" />
                <p className="mt-1 line-clamp-2 text-xs text-fg">{str(d, "name")}</p>
                {str(d, "price_display") && <p className="text-xs font-semibold text-fg">{str(d, "price_display")}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {hours.length > 0 && (
        <Section title="Opening hours">
          <Facts rows={hours.map((h) => [str(h, "day") ?? "", h.closed === true ? "Closed" : str(h, "hours")])} />
        </Section>
      )}
      <Address o={o} />
    </>
  );
}

function FlightLeg({ title, leg }: { title: string; leg: OptionItem }) {
  return (
    <Section title={title}>
      <ol className="ui-list relative space-y-4 border-l border-border pl-5">
        <li>
          <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-brand-blue bg-surface" aria-hidden />
          <p className="text-sm font-semibold text-fg">
            {str(leg, "departure_time")} {str(leg, "from_airport")}
            {str(leg, "departure_terminal") && <span className="font-normal text-fg-muted"> Terminal {str(leg, "departure_terminal")}</span>}
          </p>
          <p className="text-xs text-fg-muted">{str(leg, "departure_date")}</p>
        </li>
        {strList(leg, "layovers").map((l) => (
          <li key={l}>
            <span className="absolute -left-1 mt-1.5 h-2 w-2 rounded-full bg-rating" aria-hidden />
            <p className="text-xs text-fg">Layover in {l}</p>
          </li>
        ))}
        <li>
          <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full bg-brand-red" aria-hidden />
          <p className="text-sm font-semibold text-fg">
            {str(leg, "arrival_time")} {str(leg, "to_airport")}
            {leg.next_day === true && <span className="ml-1 text-xs text-brand-red">next day</span>}
            {str(leg, "arrival_terminal") && <span className="font-normal text-fg-muted"> Terminal {str(leg, "arrival_terminal")}</span>}
          </p>
          <p className="text-xs text-fg-muted">{str(leg, "arrival_date")}</p>
        </li>
      </ol>
      <Facts
        rows={[
          ["Flights", strList(leg, "flight_numbers").join(" + ") || null],
          ["Journey time", str(leg, "duration_display")],
          ["Aircraft", str(leg, "aircraft")],
          ["Operated by", str(leg, "codeshare")],
          ["Checked bag", str(leg, "checked_baggage")],
          ["Cabin bag", str(leg, "cabin_baggage")],
        ]}
      />
    </Section>
  );
}

function FlightBody({ o }: { o: OptionItem }) {
  const outbound = obj(o, "outbound");
  const inbound = obj(o, "inbound");
  return (
    <>
      {outbound && <FlightLeg title="Outbound" leg={outbound} />}
      {inbound && Object.keys(inbound).length > 0 && <FlightLeg title="Return" leg={inbound} />}
      <Section title="Fare">
        <Facts
          rows={[
            ["Refund", o.refundable === true ? "Refundable" : o.refundable === false ? "Non-refundable" : null],
            ["Fare type", str(o, "fare_type")],
            ["Baggage", str(o, "baggage_display")],
            ["Base fare", formatInr(num(o, "base_fare_inr"))],
            ["Taxes", formatInr(num(o, "total_tax_inr"))],
            ["Per adult", formatInr(num(o, "price_per_adult_inr"))],
            ["Total", formatInr(num(o, "price_inr"))],
          ]}
        />
      </Section>
    </>
  );
}

function HotelBody({ o }: { o: OptionItem }) {
  const rooms = objList(o, "room_policies");
  return (
    <>
      {str(o, "description_short") && (
        <Section title="About">
          <p className="text-sm leading-relaxed text-fg">{str(o, "description_short")}</p>
        </Section>
      )}
      {rooms.length > 0 && (
        <Section title="Rooms">
          <ul className="ui-list space-y-3">
            {rooms.map((r, i) => (
              <li key={i} className="rounded-xl border border-border p-3">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-medium text-fg">{str(r, "room")}</p>
                  <p className="shrink-0 text-sm font-semibold text-fg">{str(r, "total")}</p>
                </div>
                {str(r, "cancel") && <p className="mt-1 text-xs text-success">{str(r, "cancel")}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {strList(o, "amenities").length > 0 && (
        <Section title="Amenities">
          <ul className="ui-list grid grid-cols-2 gap-2 text-sm text-fg">
            {strList(o, "amenities").map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </Section>
      )}
      <Section title="Policies">
        <Facts
          rows={[
            ["Cancellation", str(o, "cancellation_display")],
            ["Refundable rates", str(o, "refundable_display")],
            ["Dining", str(o, "dining_display")],
            ["Board", str(o, "cheapest_board")],
          ]}
        />
      </Section>
      <Address o={o} />
    </>
  );
}

function TourBody({ o }: { o: OptionItem }) {
  const tourId = num(o, "tour_id");
  const optionId = num(o, "option_id") ?? str(o, "option_id");
  const supplierId = num(o, "supplier_id");
  const [sections, setSections] = useState<TourDetailSection[] | null>(null);
  const [error, setError] = useState(false);
  const prices = objList(o, "prices").length ? objList(o, "prices") : objList(o, "transfer_prices");

  useEffect(() => {
    if (tourId == null || optionId == null || supplierId == null) return;
    let live = true;
    api
      .tourOptionDetails(tourId, optionId, supplierId)
      .then((r) => live && setSections(r.sections ?? []))
      .catch(() => live && setError(true));
    return () => {
      live = false;
    };
  }, [tourId, optionId, supplierId]);

  return (
    <>
      {(str(o, "full_description") ?? str(o, "short_description")) && (
        <Section title="Overview">
          <p className="text-sm leading-relaxed text-fg">{str(o, "full_description") ?? str(o, "short_description")}</p>
        </Section>
      )}
      {prices.length > 0 && (
        <Section title={`Prices${str(o, "party") ? ` for ${str(o, "party")}` : ""}`}>
          <Facts
            rows={prices.map((p) => [
              str(p, "transfer_type") ?? "Total",
              str(p, "total_display") ?? str(p, "price_display") ?? formatInr(num(p, "price_inr")),
            ])}
          />
          <p className="mt-2 text-xs text-fg-muted">Each total includes the ticket and that transfer.</p>
        </Section>
      )}
      <Section title="Details">
        <Facts
          rows={[
            ["Duration", str(o, "duration")],
            ["Timing", str(o, "timing") ?? str(o, "slots_display")],
            ["Cancellation", str(o, "cancellation") ?? str(o, "cancellation_display")],
            ["Children", str(o, "child_policy")],
          ]}
        />
        {str(o, "price_note") && <p className="mt-2 text-xs text-fg-muted">{str(o, "price_note")}</p>}
      </Section>
      {tourId != null && optionId != null && supplierId != null && (
        <>
          {!sections && !error && (
            <Section title="Inclusions">
              <div className="space-y-2">
                <div className="skeleton h-3 w-3/4 rounded" />
                <div className="skeleton h-3 w-2/3 rounded" />
                <div className="skeleton h-3 w-1/2 rounded" />
              </div>
            </Section>
          )}
          {error && (
            <Section title="Inclusions">
              <p className="text-sm text-fg-muted">The inclusions could not be loaded. Ask the agent for them instead.</p>
            </Section>
          )}
          {sections?.map((s) => (
            <Section key={s.title} title={s.title}>
              {s.items.length ? <Bullets items={s.items} /> : <p className="text-sm text-fg">{s.summary}</p>}
            </Section>
          ))}
        </>
      )}
    </>
  );
}

function PackageBody({ o }: { o: OptionItem }) {
  const includes = strList(o, "includes").length ? strList(o, "includes") : strList(o, "inclusions");
  const day = (iso: string | null) => (iso ? iso.slice(0, 10) : null);
  return (
    <>
      {str(o, "description") && (
        <Section title="About">
          <p className="text-sm leading-relaxed text-fg">{str(o, "description")}</p>
        </Section>
      )}
      {includes.length > 0 && (
        <Section title="Included">
          <Bullets items={includes} />
        </Section>
      )}
      <Section title="Details">
        <Facts
          rows={[
            ["Destination", [str(o, "city"), str(o, "country")].filter(Boolean).join(", ") || null],
            ["Duration", num(o, "nights") ? `${num(o, "nights")} nights` : str(o, "duration")],
            ["Type", str(o, "package_type_display")],
            ["Group size", num(o, "max_pax") ? `Up to ${num(o, "max_pax")}` : null],
            ["Valid", day(str(o, "valid_from")) && day(str(o, "valid_to")) ? `${day(str(o, "valid_from"))} to ${day(str(o, "valid_to"))}` : null],
            ["Cancellation", o.is_free_cancellation === true ? "Free cancellation" : null],
          ]}
        />
      </Section>
      {strList(o, "tags").length > 0 && (
        <Section title="Good for">
          <p className="text-sm text-fg">{strList(o, "tags").join(", ")}</p>
        </Section>
      )}
    </>
  );
}

function TransferBody({ o }: { o: OptionItem }) {
  return (
    <>
      <Section title="Vehicle">
        <Facts
          rows={[
            ["Type", [str(o, "transfer_type"), str(o, "vehicle_type")].filter(Boolean).join(", ") || null],
            ["Seats", num(o, "capacity") != null ? String(num(o, "capacity")) : null],
            ["Luggage", num(o, "luggage_capacity") != null ? `${num(o, "luggage_capacity")} bags` : null],
            ["Fuel", str(o, "fuel_type")],
            ["Distance", num(o, "distance_km") != null ? `${num(o, "distance_km")} km` : null],
            ["Journey", str(o, "estimated_time")],
          ]}
        />
      </Section>
      <Section title="Price and policy">
        <Facts
          rows={[
            ["Price", formatInr(num(o, "price_inr"))],
            ["Per person", formatInr(num(o, "per_person_inr"))],
            ["Cancellation", str(o, "cancellation_policy_summary")],
          ]}
        />
        {str(o, "pricing_note") && <p className="mt-2 text-xs text-fg-muted">{str(o, "pricing_note")}</p>}
      </Section>
    </>
  );
}

function VisaBody({ o }: { o: OptionItem }) {
  const docs = objList(o, "documents");
  return (
    <>
      <Section title="Visa">
        <Facts
          rows={[
            ["Entry", str(o, "entry_type")],
            ["Stay", str(o, "stay_duration")],
            ["Validity", str(o, "validity")],
            ["Processing", str(o, "processing_time_text") ?? str(o, "processing_display")],
            ["Format", o.is_evisa === true ? "e-Visa" : null],
            ["Fee per adult", formatInr(num(o, "price_per_person_inr"))],
            ["Fee per child", formatInr(num(o, "child_price_inr"))],
          ]}
        />
      </Section>
      {docs.length > 0 ? (
        <Section title="Documents needed">
          <ul className="ui-list space-y-3">
            {docs.map((d, i) => (
              <li key={i}>
                <p className="text-sm font-medium text-fg">{str(d, "name")}</p>
                {str(d, "description") && <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">{str(d, "description")}</p>}
              </li>
            ))}
          </ul>
        </Section>
      ) : (
        strList(o, "document_requirements").length > 0 && (
          <Section title="Documents needed">
            <Bullets items={strList(o, "document_requirements")} />
          </Section>
        )
      )}
    </>
  );
}

const BODY = {
  restaurant: RestaurantBody,
  flight: FlightBody,
  hotel: HotelBody,
  tour: TourBody,
  package: PackageBody,
  transfer: TransferBody,
  visa: VisaBody,
} as const;

function headline(o: OptionItem, kind: CardRef["kind"]): { price: string | null; label: string } {
  switch (kind) {
    case "restaurant":
      return { price: formatInr(num(o, "price_per_adult_inr")) ?? str(o, "price_from_display"), label: "Per person" };
    case "flight":
      return { price: formatInr(num(o, "price_inr")), label: "Total fare" };
    case "hotel":
      return { price: formatInr(num(o, "price_inr")), label: num(o, "nights") ? `${num(o, "nights")} nights` : "Total" };
    case "transfer":
      return { price: formatInr(num(o, "price_inr")), label: "Total" };
    case "visa":
      return { price: formatInr(num(o, "price_per_person_inr")), label: "Per person" };
    case "package":
      return { price: formatInr(num(o, "price_inr")), label: "From" };
    default: {
      const totals = (objList(o, "prices").length ? objList(o, "prices") : objList(o, "transfer_prices"))
        .map((p) => num(p, "total_inr") ?? num(p, "price_inr"))
        .filter((n): n is number => n != null);
      return totals.length
        ? { price: formatInr(Math.min(...totals)), label: "From, total" }
        : { price: formatInr(num(o, "price_per_adult_inr")) ?? str(o, "price_display"), label: "Per adult" };
    }
  }
}

const BOOK_LABEL: Record<CardRef["kind"], string> = {
  restaurant: "Book table",
  flight: "Book now",
  hotel: "Book now",
  tour: "Book now",
  package: "Enquire",
  transfer: "Book now",
  visa: "Apply",
};

/**
 * The detail view for any card. Data comes from the card itself (the fast
 * path); tours additionally load their inclusions and policies on open.
 */
export function DetailPanel({
  card,
  onClose,
  onBook,
  onAsk,
}: {
  card: CardRef | null;
  onClose: () => void;
  onBook: (card: CardRef) => void;
  onAsk: (card: CardRef) => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!card) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [card, onClose]);

  if (!card) return null;
  const o = card.item;
  const Body = BODY[card.kind];
  const title = titleOf(o, card.kind);
  const { price, label } = headline(o, card.kind);
  const images = card.kind === "flight" || card.kind === "visa" ? [] : imagesOf(o);

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <div className="panel-in absolute inset-y-0 right-0 flex w-full flex-col bg-surface shadow-2xl sm:w-[480px] sm:border-l sm:border-border">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="relative">
            {card.kind !== "flight" && card.kind !== "visa" ? (
              <Gallery images={images} alt={title} kind={card.kind} />
            ) : (
              <div className="h-16" />
            )}
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="press absolute top-3 left-3 grid h-9 w-9 place-items-center rounded-full bg-white/90 text-brand-black shadow-sm hover:bg-white"
            >
              <X size={18} aria-hidden />
            </button>
            <HeartButton card={card} className="absolute top-3 right-3" />
          </div>

          <header className="px-5 pt-4 pb-4">
            <div className="flex items-start justify-between gap-3">
              <h2 className="text-xl leading-snug font-semibold tracking-tight text-fg">{title}</h2>
              <RatingPill value={num(o, "rating")} />
            </div>
          </header>

          <Body o={o} />
        </div>

        <footer className="flex items-center gap-3 border-t border-border bg-surface px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <div className="min-w-0 flex-1">
            {price ? (
              <>
                <p className="text-[11px] text-fg-muted">{label}</p>
                <p className="text-lg leading-tight font-semibold tracking-tight text-fg">{price}</p>
              </>
            ) : (
              <p className="text-sm text-fg-muted">Price on request</p>
            )}
          </div>
          <button
            type="button"
            onClick={() => onAsk(card)}
            className="press inline-flex h-10 items-center gap-1.5 rounded-[10px] border border-border px-3 text-sm font-medium text-fg hover:bg-surface-2"
          >
            <MessageCircle size={15} aria-hidden />
            Ask the agent
          </button>
          <button
            type="button"
            onClick={() => onBook(card)}
            className="press inline-flex h-10 items-center rounded-[10px] bg-brand-blue px-4 text-sm font-medium text-white hover:bg-brand-blue-dark"
          >
            {BOOK_LABEL[card.kind]}
          </button>
        </footer>
      </div>
    </div>
  );
}
