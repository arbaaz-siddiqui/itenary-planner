"use client";

import {
  BedDouble,
  Briefcase,
  CarFront,
  Clock,
  FileCheck2,
  Headphones,
  Leaf,
  Luggage,
  Map,
  Package,
  Plane,
  ShieldCheck,
  Sparkles,
  Users,
  UtensilsCrossed,
} from "lucide-react";
import { useState } from "react";
import type { CardRef } from "@/lib/cardActions";
import { partyTotalNote, formatInr, imagesOf, num, obj, objList, str, strList, titleOf } from "@/lib/cards";
import type { OptionItem } from "@/lib/types";
import { SafeImage } from "../SafeImage";
import { BookButton, CardShell, HeartButton, MetaLine, PriceTag, RatingPill, TagChips } from "./parts";

type CardProps = { card: CardRef; eager?: boolean };

/** "07:30 AM" + "11:45 PM" -> "16h 15m". Null when either time is unreadable. */
export function openDuration(open: string | null, close: string | null): string | null {
  const mins = (t: string | null) => {
    const m = t?.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i);
    if (!m) return null;
    let h = Number(m[1]) % 12;
    if (m[3]?.toUpperCase() === "PM") h += 12;
    if (!m[3]) h = Number(m[1]);
    return h * 60 + Number(m[2]);
  };
  const a = mins(open);
  let b = mins(close);
  if (a == null || b == null) return null;
  if (b <= a) b += 24 * 60;
  const d = b - a;
  return `${Math.floor(d / 60)}h ${d % 60}m`;
}

function Photo({ card, icon, eager, contain }: CardProps & { icon: typeof Map; contain?: boolean }) {
  return (
    <div className="relative">
      <SafeImage
        src={imagesOf(card.item)[0]}
        alt={titleOf(card.item, card.kind)}
        icon={icon}
        eager={eager}
        className={contain ? "bg-white [&_img]:object-contain [&_img]:p-3" : ""}
      />
      <HeartButton card={card} className="absolute top-3 right-3" />
    </div>
  );
}

// --------------------------------------------------------------- restaurant
export function RestaurantCard({ card, eager }: CardProps) {
  const o = card.item;
  const opens = str(o, "opening_time");
  const closes = str(o, "closing_time");
  const price = formatInr(num(o, "price_per_adult_inr")) ?? str(o, "price_from_display");
  return (
    <CardShell card={card}>
      <Photo card={card} icon={UtensilsCrossed} eager={eager} />
      <div className="flex flex-1 flex-col gap-2.5 p-4">
        <div className="flex items-start justify-between gap-2">
          <h4 className="line-clamp-2 text-base leading-snug font-semibold text-fg">{titleOf(o, "restaurant")}</h4>
          <RatingPill value={num(o, "rating")} />
        </div>
        <MetaLine
          items={[
            opens && `Opens ${opens}`,
            openDuration(opens, closes),
            closes && `Closes ${closes}`,
            str(o, "cuisine"),
          ]}
        />
        <TagChips tags={strList(o, "tags")} />
        <div className="mt-auto flex items-end justify-between gap-3 pt-1">
          <div>
            {str(o, "veg_type") && (
              <p className="mb-1 inline-flex items-center gap-1 text-xs font-medium text-success">
                <Leaf size={13} aria-hidden />
                {str(o, "veg_type")}
              </p>
            )}
            <PriceTag value={price} label="Per person" note={partyTotalNote(o)} align="left" />
          </div>
          <BookButton card={card} label="Book table" />
        </div>
      </div>
    </CardShell>
  );
}

// --------------------------------------------------------------------- tour
export function TourCard({ card, eager }: CardProps) {
  const o = card.item;
  const prices = objList(o, "prices").length ? objList(o, "prices") : objList(o, "transfer_prices");
  const totals = prices.map((p) => num(p, "total_inr") ?? num(p, "price_inr")).filter((n): n is number => n != null);
  const pax = prices.map((p) => num(p, "priced_for_pax")).find((n) => n != null);
  // Every tour card shows the same unit, the price per person, so cards can
  // be compared at a glance; the reply text gives the total for the party.
  // A party total is split evenly across the travellers it was priced for.
  const perPerson = totals.length && pax ? Math.min(...totals) / pax : null;
  const price =
    formatInr(perPerson != null ? Math.round(perPerson * 100) / 100 : num(o, "price_per_adult_inr")) ??
    str(o, "price_display");
  const cheapest = prices.find((p) => (num(p, "total_inr") ?? num(p, "price_inr")) === Math.min(...totals));
  const withTransfer = perPerson != null && !/without/i.test(str(cheapest, "transfer_type") ?? "");
  const priceLabel = withTransfer ? "From, per person with transfer" : "From, per person";
  const cancel = str(o, "cancellation") ?? str(o, "cancellation_display");
  const free = cancel ? /free/i.test(cancel) : false;
  return (
    <CardShell card={card}>
      <Photo card={card} icon={Map} eager={eager} />
      <div className="flex flex-1 flex-col gap-2.5 p-4">
        <div className="flex items-start justify-between gap-2">
          <h4 className="line-clamp-2 text-base leading-snug font-semibold text-fg">{titleOf(o, "tour")}</h4>
          <RatingPill value={num(o, "rating")} />
        </div>
        <MetaLine items={[str(o, "category"), str(o, "duration"), str(o, "timing") ?? str(o, "slots_display")]} />
        {o.is_addon === true && <p className="text-xs text-fg-muted">Add-on: book with a main option</p>}
        {cancel && <p className={`text-xs ${free ? "text-success" : "text-fg-muted"}`}>{cancel}</p>}
        <div className="mt-auto flex items-end justify-between gap-3 pt-1">
          <PriceTag value={price} label={priceLabel} note={partyTotalNote(o)} align="left" />
          <BookButton card={card} />
        </div>
      </div>
    </CardShell>
  );
}

// -------------------------------------------------------------------- hotel
export function HotelCard({ card, eager }: CardProps) {
  const o = card.item;
  const stars = Math.round(num(o, "stars") ?? 0);
  const nights = num(o, "nights");
  const free = o.has_free_cancellation === true;
  return (
    <CardShell card={card}>
      <Photo card={card} icon={BedDouble} eager={eager} />
      <div className="flex flex-1 flex-col gap-2.5 p-4">
        <div>
          <h4 className="line-clamp-2 text-base leading-snug font-semibold text-fg">{titleOf(o, "hotel")}</h4>
          {stars > 0 && (
            <p className="mt-0.5 text-sm text-rating" aria-label={`${stars} star hotel`}>
              {"★".repeat(stars)}
            </p>
          )}
        </div>
        <MetaLine items={[str(o, "area") || str(o, "full_address"), str(o, "cheapest_room_type"), str(o, "cheapest_board")]} />
        <TagChips tags={strList(o, "amenities")} max={3} />
        {free && <p className="text-xs font-medium text-success">Free cancellation</p>}
        <div className="mt-auto flex items-end justify-between gap-3 pt-1">
          <PriceTag
            value={formatInr(num(o, "per_night_inr")) ?? formatInr(num(o, "price_inr"))}
            unit={num(o, "per_night_inr") != null ? "/night" : null}
            note={
              nights && num(o, "per_night_inr") != null && num(o, "price_inr") != null
                ? `${formatInr(num(o, "price_inr"))} for ${nights} ${nights === 1 ? "night" : "nights"}`
                : null
            }
            align="left"
          />
          <BookButton card={card} />
        </div>
      </div>
    </CardShell>
  );
}

// ------------------------------------------------------------------ package
export function PackageCard({ card, eager }: CardProps) {
  const o = card.item;
  const nights = num(o, "nights");
  const includes = strList(o, "includes").length ? strList(o, "includes") : strList(o, "inclusions");
  return (
    <CardShell card={card}>
      <Photo card={card} icon={Package} eager={eager} />
      <div className="flex flex-1 flex-col gap-2.5 p-4">
        <div className="flex items-start justify-between gap-2">
          <h4 className="line-clamp-2 text-base leading-snug font-semibold text-fg">{titleOf(o, "package")}</h4>
          <RatingPill value={num(o, "rating")} />
        </div>
        <MetaLine
          items={[
            str(o, "city"),
            nights ? `${nights} nights, ${nights + 1} days` : str(o, "duration"),
            str(o, "package_type_display"),
          ]}
        />
        <TagChips tags={includes.length ? includes : strList(o, "tags")} max={3} />
        {o.is_free_cancellation === true && <p className="text-xs font-medium text-success">Free cancellation</p>}
        <div className="mt-auto flex items-end justify-between gap-3 pt-1">
          <PriceTag
            value={formatInr(num(o, "price_inr") ?? num(o, "price_from_inr")) ?? "Price on request"}
            label={num(o, "price_inr") ?? num(o, "price_from_inr") ? "From" : null}
            align="left"
          />
          <BookButton card={card} label="View package" />
        </div>
      </div>
    </CardShell>
  );
}

// ----------------------------------------------------------------- transfer
export function TransferCard({ card, eager }: CardProps) {
  const o = card.item;
  const type = str(o, "transfer_type");
  const shared = type ? /shar/i.test(type) : false;
  return (
    <CardShell card={card}>
      <Photo card={card} icon={CarFront} eager={eager} contain />
      <div className="flex flex-1 flex-col gap-2.5 p-4">
        <div className="flex items-start justify-between gap-2">
          <h4 className="text-base leading-snug font-semibold text-fg">{titleOf(o, "transfer")}</h4>
          {type && (
            <span className="shrink-0 rounded-full bg-brand-blue/10 px-2 py-0.5 text-[11px] font-medium text-link">
              {type}
            </span>
          )}
        </div>
        <ul className="ui-list flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
          {num(o, "capacity") != null && (
            <li className="inline-flex items-center gap-1">
              <Users size={13} aria-hidden /> {num(o, "capacity")} seats
            </li>
          )}
          {num(o, "luggage_capacity") != null && (
            <li className="inline-flex items-center gap-1">
              <Luggage size={13} aria-hidden /> {num(o, "luggage_capacity")} bags
            </li>
          )}
          {str(o, "estimated_time") && (
            <li className="inline-flex items-center gap-1">
              <Clock size={13} aria-hidden /> {str(o, "estimated_time")}
            </li>
          )}
        </ul>
        {str(o, "cancellation_policy_summary") && (
          <p className="text-xs text-fg-muted">{str(o, "cancellation_policy_summary")}</p>
        )}
        <div className="mt-auto flex items-end justify-between gap-3 pt-1">
          <PriceTag
            value={formatInr(num(o, "price_inr"))}
            label={shared ? "Per person" : "Per vehicle"}
            align="left"
          />
          <BookButton card={card} />
        </div>
      </div>
    </CardShell>
  );
}

// --------------------------------------------------------------------- visa
const TRUST = [
  { icon: ShieldCheck, label: "Secure" },
  { icon: Sparkles, label: "Trusted experts" },
  { icon: Headphones, label: "24x7 support" },
  { icon: Briefcase, label: "Hassle-free" },
];

export function VisaCard({ card }: CardProps) {
  const o = card.item;
  return (
    <CardShell card={card}>
      <div className="relative flex aspect-[3/2] flex-col justify-end overflow-hidden bg-[radial-gradient(ellipse_120%_100%_at_100%_0%,#2b7fc8_0%,#1f5f9e_45%,#0b0b0f_100%)] p-4 text-white">
        <FileCheck2 size={96} strokeWidth={1} className="absolute -top-2 -right-2 text-white/15" aria-hidden />
        <p className="text-xs font-medium text-white/80">{o.is_evisa === true ? "e-Visa" : "Visa"}</p>
        <h4 className="text-lg leading-snug font-semibold">{titleOf(o, "visa")}</h4>
        <HeartButton card={card} className="absolute top-3 right-3" />
      </div>
      <div className="flex flex-1 flex-col gap-3 p-4">
        <ul className="ui-list space-y-1.5 text-sm text-fg">
          {str(o, "entry_type") && <li>{str(o, "entry_type")} entry</li>}
          {str(o, "stay_duration") && <li>Stay up to {str(o, "stay_duration")}</li>}
          {(str(o, "processing_time_text") ?? str(o, "processing_display")) && (
            <li>Processing in {str(o, "processing_time_text") ?? str(o, "processing_display")}</li>
          )}
        </ul>
        <ul className="ui-list grid grid-cols-2 gap-1.5 text-[11px] text-fg-muted">
          {TRUST.map(({ icon: Icon, label }) => (
            <li key={label} className="inline-flex items-center gap-1.5">
              <Icon size={13} className="text-brand-blue" aria-hidden />
              {label}
            </li>
          ))}
        </ul>
        <div className="mt-auto flex items-end justify-between gap-3 pt-1">
          <PriceTag
            value={formatInr(num(o, "price_per_person_inr")) ?? "On request"}
            label="Per person"
            note={partyTotalNote(o)}
            align="left"
          />
          <BookButton card={card} label="Apply" />
        </div>
      </div>
    </CardShell>
  );
}

// ------------------------------------------------------------------- flight
function LegRow({ leg, fallbackRoute }: { leg: OptionItem; fallbackRoute: string | null }) {
  const stops = num(leg, "stops") ?? 0;
  const via = strList(leg, "layovers").map((l) => l.split(" ")[0]).filter(Boolean);
  const from = str(leg, "from_airport");
  const to = str(leg, "to_airport");
  const [routeFrom, routeTo] = (fallbackRoute ?? "").split(/\s*(?:→|->|-)\s*/);
  return (
    <div className="grid grid-cols-[auto_1fr_auto] items-center gap-3 sm:gap-5">
      <div>
        <p className="text-xl leading-none font-semibold tabular-nums text-fg">{str(leg, "departure_time") ?? "--:--"}</p>
        <p className="mt-1 text-xs font-medium text-fg-muted">{from ?? routeFrom ?? ""}</p>
      </div>
      <div className="min-w-0 text-center">
        <p className="text-[11px] text-fg-muted">{str(leg, "duration_display")}</p>
        <div className="relative my-1 h-px bg-border">
          <Plane size={14} className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 rotate-90 bg-surface px-0.5 text-brand-red" aria-hidden />
        </div>
        <span
          className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${
            stops === 0 ? "bg-success/15 text-success" : "bg-rating/20 text-fg"
          }`}
        >
          {stops === 0 ? "Non-stop" : `${stops} stop${stops > 1 ? "s" : ""}${via.length ? ` via ${via.join(", ")}` : ""}`}
        </span>
      </div>
      <div className="text-right">
        <p className="text-xl leading-none font-semibold tabular-nums text-fg">
          {str(leg, "arrival_time") ?? "--:--"}
          {leg.next_day === true && <sup className="ml-0.5 text-[10px] font-semibold text-brand-red">+1</sup>}
        </p>
        <p className="mt-1 text-xs font-medium text-fg-muted">{to ?? routeTo ?? ""}</p>
      </div>
    </div>
  );
}

const CABINS: Record<string, string> = { Y: "Economy", S: "Premium economy", C: "Business", F: "First" };

/** The airlines' own logos (from the supplier, on the CDN), stacked when a
 *  trip flies more than one. Falls back to the airline code badge. */
function AirlineLogos({ urls, code }: { urls: string[]; code: string | null }) {
  const [failed, setFailed] = useState<string[]>([]);
  const shown = urls.filter((u) => !failed.includes(u)).slice(0, 2);
  if (!shown.length) {
    return (
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand-red/10 text-xs font-bold text-brand-red">
        {code ?? <Plane size={16} aria-hidden />}
      </span>
    );
  }
  return (
    <span className="flex shrink-0 -space-x-2">
      {shown.map((u) => (
        <span key={u} className="grid h-10 w-10 place-items-center overflow-hidden rounded-full bg-white p-1.5 ring-2 ring-surface">
          <img
            src={u}
            alt=""
            aria-hidden
            loading="lazy"
            className="h-full w-full object-contain"
            onError={() => setFailed((f) => [...f, u])}
          />
        </span>
      ))}
    </span>
  );
}

export function FlightRow({ card }: CardProps) {
  const o = card.item;
  const outbound = obj(o, "outbound");
  const inbound = obj(o, "inbound");
  const flightNos = strList(outbound, "flight_numbers");
  const code = str(o, "airline_code");
  const seats = num(outbound, "seats_remaining");
  const pax = num(o, "pax_count");
  return (
    <CardShell card={card} className="!h-auto">
      <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center">
        <div className="flex min-w-0 items-center gap-3 sm:w-44">
          <AirlineLogos urls={strList(o, "airline_logos")} code={code} />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-fg">{titleOf(o, "flight")}</p>
            <p className="truncate text-xs text-fg-muted">
              {flightNos.length ? `${code ? `${code} ` : ""}${flightNos.join(" + ")}` : str(o, "cabin") && CABINS[str(o, "cabin")!]}
            </p>
          </div>
        </div>
        <div className="flex flex-1 flex-col gap-3">
          {outbound ? <LegRow leg={outbound} fallbackRoute={str(o, "route_outbound")} /> : <MetaLine items={[str(o, "route_outbound")]} />}
          {inbound && Object.keys(inbound).length > 0 && <LegRow leg={inbound} fallbackRoute={str(o, "route_return")} />}
        </div>
        <div className="flex items-center justify-between gap-3 sm:w-44 sm:flex-col sm:items-end">
          <div className="sm:text-right">
            {o.refundable === true && (
              <span className="mb-1 inline-block rounded-full bg-success/15 px-2 py-0.5 text-[10px] font-semibold text-success">Refundable</span>
            )}
            <PriceTag value={formatInr(num(o, "price_inr"))} label={pax ? `Total, ${pax} traveller${pax > 1 ? "s" : ""}` : "Total"} />
          </div>
          <BookButton card={card} />
        </div>
      </div>
      <MetaLine
        className="border-t border-border px-4 py-2"
        items={[
          str(o, "cabin") ? (CABINS[str(o, "cabin")!] ?? str(o, "cabin")) : null,
          str(o, "baggage_display") && `Baggage ${str(o, "baggage_display")}`,
          seats != null && seats > 0 && seats < 10 ? `${seats} seats left` : null,
          str(o, "fare_type"),
        ]}
      />
    </CardShell>
  );
}

export const CARD_FOR = {
  restaurant: RestaurantCard,
  tour: TourCard,
  hotel: HotelCard,
  package: PackageCard,
  transfer: TransferCard,
  visa: VisaCard,
  flight: FlightRow,
} as const;

export const KIND_ICON = {
  restaurant: UtensilsCrossed,
  tour: Map,
  hotel: BedDouble,
  package: Package,
  transfer: CarFront,
  visa: FileCheck2,
  flight: Plane,
} as const;
