"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, ExternalLink, MapPin, MessageCircle, Plane, X } from "lucide-react";
import { api } from "@/lib/api";
import type { CardRef } from "@/lib/cardActions";
import { formatInr, imagesOf, mergeImages, num, obj, objList, str, strList, titleOf } from "@/lib/cards";
import type { OptionItem, TourDetailSection } from "@/lib/types";
import { SafeImage } from "../SafeImage";
import { HeartButton, RatingPill } from "../cards/parts";
import { KIND_ICON, openDuration } from "../cards/Cards";

// ------------------------------------------------------------------ pieces
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="px-5 py-4">
      <h3 className="mb-3 text-[15px] font-semibold tracking-tight text-fg">{title}</h3>
      {children}
    </section>
  );
}

/**
 * Facts as tiles, two to a row: a label above its value reads faster than a
 * two-column table, and long values (a list of cuisines) take a full row.
 */
function Facts({ rows }: { rows: [string, string | null | undefined][] }) {
  const shown = rows.filter((r): r is [string, string] => typeof r[1] === "string" && r[1].trim() !== "");
  if (!shown.length) return null;
  return (
    <dl className="grid grid-cols-2 gap-2">
      {shown.map(([k, v]) => (
        <div
          key={k}
          className={`rounded-xl border border-border bg-bg/40 px-3 py-2.5 ${v.length > 26 ? "col-span-2" : ""}`}
        >
          <dt className="text-[11px] text-fg-muted">{k}</dt>
          <dd className="mt-0.5 text-sm leading-snug font-medium text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A price breakdown read like a bill: lines, then the total in bold. */
function Receipt({ rows, total }: { rows: [string, string | null][]; total: [string, string | null] }) {
  const shown = rows.filter((r): r is [string, string] => Boolean(r[1]));
  if (!shown.length && !total[1]) return null;
  return (
    <div className="rounded-xl border border-border bg-bg/40 p-4">
      <dl className="space-y-2 text-sm">
        {shown.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-4">
            <dt className="text-fg-muted">{k}</dt>
            <dd className="font-medium tabular-nums text-fg">{v}</dd>
          </div>
        ))}
      </dl>
      {total[1] && (
        <div className="mt-3 flex items-baseline justify-between gap-4 border-t border-border pt-3">
          <span className="text-sm font-semibold text-fg">{total[0]}</span>
          <span className="text-lg font-semibold tracking-tight tabular-nums text-fg">{total[1]}</span>
        </div>
      )}
    </div>
  );
}

/** Small facts under the title, the ones a customer decides on. */
function Highlights({ items }: { items: { text: string; tone?: "good" | "brand" }[] }) {
  const shown = items.filter((i) => i.text);
  if (!shown.length) return null;
  return (
    <ul className="ui-list mt-3 flex flex-wrap gap-1.5">
      {shown.map((i) => (
        <li
          key={i.text}
          className={`rounded-full px-2.5 py-1 text-xs font-medium ${
            i.tone === "good"
              ? "bg-success/15 text-success"
              : i.tone === "brand"
                ? "bg-brand-blue/15 text-link"
                : "bg-surface-2 text-fg"
          }`}
        >
          {i.text}
        </li>
      ))}
    </ul>
  );
}

function Bullets({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <ul className="ui-list space-y-2 text-sm text-fg">
      {items.map((t, i) => (
        <li key={i} className="flex gap-2.5">
          <Check size={16} className="mt-0.5 shrink-0 text-success" aria-hidden />
          <span className="leading-relaxed">{t}</span>
        </li>
      ))}
    </ul>
  );
}

function Gallery({ images, alt, kind }: { images: string[]; alt: string; kind: CardRef["kind"] }) {
  const [index, setIndex] = useState(0);
  const strip = useRef<HTMLDivElement>(null);
  const shown = images.length ? images : [""];
  const go = (i: number) => {
    const el = strip.current;
    if (!el) return;
    const next = Math.max(0, Math.min(shown.length - 1, i));
    el.scrollTo({ left: next * el.clientWidth, behavior: "smooth" });
  };
  const arrow =
    "press absolute top-1/2 z-10 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full bg-white/90 text-brand-black shadow-md backdrop-blur hover:bg-white disabled:pointer-events-none disabled:opacity-0";
  return (
    <div
      className="relative"
      tabIndex={shown.length > 1 ? 0 : -1}
      aria-label={`${alt} photos`}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") go(index + 1);
        if (e.key === "ArrowLeft") go(index - 1);
      }}
    >
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
        <>
          <button type="button" onClick={() => go(index - 1)} disabled={index === 0} aria-label="Previous photo" className={`${arrow} left-3`}>
            <ChevronLeft size={18} aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => go(index + 1)}
            disabled={index >= shown.length - 1}
            aria-label="Next photo"
            className={`${arrow} right-3`}
          >
            <ChevronRight size={18} aria-hidden />
          </button>
          <span className="absolute right-3 bottom-3 rounded-full bg-black/55 px-2 py-0.5 text-[11px] font-medium text-white tabular-nums">
            {index + 1} / {shown.length}
          </span>
        </>
      )}
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
    <Section title={`${title} details`}>
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
      <div className="mt-4" />
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

function RouteSummary({ leg, label }: { leg: OptionItem; label: string }) {
  const stops = num(leg, "stops") ?? 0;
  return (
    <div className="rounded-2xl border border-border bg-bg/40 p-4">
      <p className="mb-3 text-xs font-medium text-fg-muted">{label}</p>
      <div className="grid grid-cols-[auto_1fr_auto] items-center gap-4">
        <div>
          <p className="text-2xl leading-none font-semibold tabular-nums text-fg">{str(leg, "departure_time")}</p>
          <p className="mt-1 text-sm font-medium text-fg-muted">{str(leg, "from_airport")}</p>
        </div>
        <div className="text-center">
          <p className="text-xs text-fg-muted">{str(leg, "duration_display")}</p>
          <div className="relative my-1.5 h-px bg-border">
            <Plane size={15} className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 rotate-90 bg-surface px-0.5 text-brand-red" aria-hidden />
          </div>
          <p className="text-xs text-fg-muted">{stops === 0 ? "Non-stop" : `${stops} stop${stops > 1 ? "s" : ""}`}</p>
        </div>
        <div className="text-right">
          <p className="text-2xl leading-none font-semibold tabular-nums text-fg">
            {str(leg, "arrival_time")}
            {leg.next_day === true && <sup className="ml-0.5 text-xs text-brand-red">+1</sup>}
          </p>
          <p className="mt-1 text-sm font-medium text-fg-muted">{str(leg, "to_airport")}</p>
        </div>
      </div>
    </div>
  );
}

function FlightBody({ o }: { o: OptionItem }) {
  const outbound = obj(o, "outbound");
  const inbound = obj(o, "inbound");
  return (
    <>
      <div className="space-y-2 px-5 pb-2">
        {outbound && <RouteSummary leg={outbound} label={`Outbound${str(outbound, "departure_date") ? `, ${str(outbound, "departure_date")}` : ""}`} />}
        {inbound && Object.keys(inbound).length > 0 && (
          <RouteSummary leg={inbound} label={`Return${str(inbound, "departure_date") ? `, ${str(inbound, "departure_date")}` : ""}`} />
        )}
      </div>
      {outbound && <FlightLeg title="Outbound" leg={outbound} />}
      {inbound && Object.keys(inbound).length > 0 && <FlightLeg title="Return" leg={inbound} />}
      <Section title="Fare">
        <Receipt
          rows={[
            ["Base fare", formatInr(num(o, "base_fare_inr"))],
            ["Taxes and fees", formatInr(num(o, "total_tax_inr"))],
            ["Per adult", formatInr(num(o, "price_per_adult_inr"))],
          ]}
          total={[num(o, "pax_count") ? `Total for ${num(o, "pax_count")}` : "Total", formatInr(num(o, "price_inr"))]}
        />
        <div className="mt-2">
          <Facts
            rows={[
              ["Refund", o.refundable === true ? "Refundable" : o.refundable === false ? "Non-refundable" : null],
              ["Fare type", str(o, "fare_type")],
              ["Baggage", str(o, "baggage_display")],
            ]}
          />
        </div>
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
        <Section
          title={(() => {
            const pax = prices.map((p) => num(p, "priced_for_pax")).find((n) => n != null);
            const who = str(o, "party") ?? (pax ? `${pax} traveller${pax > 1 ? "s" : ""}` : null);
            return who ? `Total for ${who}` : "Total prices";
          })()}
        >
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
          {sections?.map((s, i) => (
            <Section key={`${s.title}-${i}`} title={s.title}>
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
          ]}
        />
      </Section>
      <Section title="Fees">
        <Receipt
          rows={[["Per child", formatInr(num(o, "child_price_inr"))]]}
          total={["Per adult", formatInr(num(o, "price_per_person_inr"))]}
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

/** "GF 65 + GF 512": the supplier sends bare numbers, the airline code apart. */
function flightNumbers(o: OptionItem): string | null {
  const code = str(o, "airline_code");
  const nums = strList(obj(o, "outbound"), "flight_numbers");
  if (!nums.length) return null;
  return nums.map((n) => (code && !n.toUpperCase().startsWith(code.toUpperCase()) ? `${code} ${n}` : n)).join(" + ");
}

function subtitle(o: OptionItem, kind: CardRef["kind"]): string | null {
  switch (kind) {
    case "restaurant":
      return [str(o, "area"), str(o, "cuisine")].filter(Boolean).join(", ") || null;
    case "hotel":
      return str(o, "area") || str(o, "full_address");
    case "flight":
      return [str(o, "route_outbound"), flightNumbers(o)]
        .filter(Boolean)
        .join(", ") || null;
    case "tour":
      return [str(o, "category"), str(o, "duration")].filter(Boolean).join(", ") || null;
    case "package":
      return [str(o, "city"), num(o, "nights") ? `${num(o, "nights")} nights` : null].filter(Boolean).join(", ") || null;
    case "transfer":
      return [str(o, "vehicle_type"), str(o, "estimated_time")].filter(Boolean).join(", ") || null;
    case "visa":
      return str(o, "validity") ? `Valid ${str(o, "validity")!.toLowerCase()}` : null;
  }
}

function highlights(o: OptionItem, kind: CardRef["kind"]): { text: string; tone?: "good" | "brand" }[] {
  const out: { text: string; tone?: "good" | "brand" }[] = [];
  const add = (text: string | null | undefined | false, tone?: "good" | "brand") => {
    if (text) out.push({ text, tone });
  };
  switch (kind) {
    case "restaurant":
      add(str(o, "veg_type"), "good");
      add(str(o, "opening_time") && str(o, "closing_time") && `${str(o, "opening_time")} to ${str(o, "closing_time")}`);
      strList(o, "tags").slice(0, 2).forEach((t) => add(t, "brand"));
      break;
    case "hotel": {
      const stars = Math.round(num(o, "stars") ?? 0);
      add(stars > 0 && `${stars}-star`);
      add(o.has_free_cancellation === true && "Free cancellation", "good");
      add(str(o, "cheapest_board"));
      break;
    }
    case "flight":
      add(o.refundable === true ? "Refundable" : o.refundable === false ? "Non-refundable" : null, o.refundable === true ? "good" : undefined);
      add(str(o, "baggage_display") && `Bags ${str(o, "baggage_display")}`);
      add(num(obj(o, "outbound"), "seats_remaining") != null && `${num(obj(o, "outbound"), "seats_remaining")} seats left`, "brand");
      break;
    case "tour": {
      const cancel = str(o, "cancellation") ?? str(o, "cancellation_display");
      add(cancel, cancel && /free/i.test(cancel) ? "good" : undefined);
      add(str(o, "timing") ?? str(o, "slots_display"));
      break;
    }
    case "package":
      add(o.is_free_cancellation === true && "Free cancellation", "good");
      strList(o, "tags").slice(0, 3).forEach((t) => add(t, "brand"));
      break;
    case "transfer":
      add(str(o, "transfer_type"), "brand");
      add(num(o, "capacity") != null && `${num(o, "capacity")} seats`);
      add(num(o, "luggage_capacity") != null && `${num(o, "luggage_capacity")} bags`);
      break;
    case "visa":
      add(o.is_evisa === true && "e-Visa", "brand");
      add(str(o, "stay_duration") && `Stay ${str(o, "stay_duration")}`);
      add(str(o, "processing_time_text") && `Ready in ${str(o, "processing_time_text")}`, "good");
      break;
  }
  return out;
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
/** A tour search carries one photo per tour; the rest are loaded when its
 *  card opens. Until they arrive (or if they never do) the card's photo shows. */
function useTourGallery(card: CardRef | null): string[] {
  const tourId = card?.kind === "tour" ? num(card.item, "tour_id") : null;
  const [loaded, setLoaded] = useState<{ id: number; images: string[] } | null>(null);
  useEffect(() => {
    if (tourId == null) return;
    let live = true;
    api
      .tourImages(tourId)
      .then((r) => live && setLoaded({ id: tourId, images: r.images ?? [] }))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [tourId]);
  return loaded && loaded.id === tourId ? loaded.images : [];
}

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
  const gallery = useTourGallery(card);

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
  const images = card.kind === "flight" || card.kind === "visa" ? [] : mergeImages(imagesOf(o), gallery);

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <div className="panel-in absolute inset-y-0 right-0 flex w-full flex-col bg-surface shadow-2xl sm:w-[480px] sm:border-l sm:border-border">
        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
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

          <header className="px-5 pt-5 pb-3">
            <div className="flex items-start justify-between gap-3">
              <h2 className="text-[22px] leading-tight font-semibold tracking-tight text-fg">{title}</h2>
              <RatingPill value={num(o, "rating")} />
            </div>
            {subtitle(o, card.kind) && <p className="mt-1 text-sm text-fg-muted">{subtitle(o, card.kind)}</p>}
            <Highlights items={highlights(o, card.kind)} />
          </header>

          <div className="divide-y divide-border border-t border-border pb-4">
            <Body o={o} />
          </div>
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
            aria-label="Ask the agent"
            className="press inline-flex h-10 shrink-0 items-center gap-1.5 rounded-[10px] border border-border px-3 text-sm font-medium text-fg hover:bg-surface-2"
          >
            <MessageCircle size={15} aria-hidden />
            <span className="max-[400px]:hidden">Ask the agent</span>
          </button>
          <button
            type="button"
            onClick={() => onBook(card)}
            className="press inline-flex h-10 shrink-0 items-center rounded-[10px] bg-brand-blue px-4 text-sm font-medium whitespace-nowrap text-white hover:bg-brand-blue-dark"
          >
            {BOOK_LABEL[card.kind]}
          </button>
        </footer>
      </div>
    </div>
  );
}
