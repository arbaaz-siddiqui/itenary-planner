"use client";

import { SafeImage } from "./SafeImage";
import { Carousel } from "./Carousel";
import { TourDetails } from "./TourDetails";
import type { OptionGroup, OptionItem } from "@/lib/types";

// Field names mirror what the Python agent already emits (see the Streamlit
// renderers in surfaces/streamlit_app.py): tours carry `image_url`, hotels
// carry an `image_urls` array. Everything is read defensively — the contract
// does not pin the item shape, so a missing key must never blank the card.

const str = (o: OptionItem, k: string): string | null => {
  const v = o[k];
  return typeof v === "string" && v.trim() ? v : null;
};

const num = (o: OptionItem, k: string): number | null => {
  const v = o[k];
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() && Number.isFinite(Number(v))) return Number(v);
  return null;
};

const inr = (n: number | null): string | null =>
  n == null ? null : `₹${Math.round(n).toLocaleString("en-IN")}`;

function firstImage(o: OptionItem): unknown {
  const single = o.image_url;
  if (typeof single === "string" && single) return single;
  const many = o.image_urls;
  if (Array.isArray(many) && many.length) return many[0];
  return null;
}

function Price({ value, note }: { value: string | null; note?: string | null }) {
  if (!value) return null;
  return (
    <div className="shrink-0 text-right">
      <div className="text-base font-semibold tabular-nums">{value}</div>
      {note && <div className="text-[11px] text-fg-muted">{note}</div>}
    </div>
  );
}

function Meta({ items }: { items: (string | null)[] }) {
  const shown = items.filter(Boolean) as string[];
  if (!shown.length) return null;
  return (
    <p className="mt-1 text-xs leading-relaxed text-fg-muted">{shown.join(" · ")}</p>
  );
}

function HotelCard({ o }: { o: OptionItem }) {
  const name = str(o, "hotel_name") ?? str(o, "name") ?? "Hotel";
  const stars = num(o, "stars") ?? 0;
  const nights = num(o, "nights");
  const perNight = inr(num(o, "per_night_inr"));
  const free = o.has_free_cancellation === true;
  const cancel = str(o, "cancellation_display");

  return (
    <article className="flex gap-3 rounded-xl border border-border bg-bg p-3">
      <SafeImage src={firstImage(o)} alt={name} className="w-28 shrink-0" ratio="aspect-square" />
      <div className="flex min-w-0 flex-1 gap-3">
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-sm font-medium">
            {name}
            {stars > 0 && <span className="ml-1.5 text-amber-500">{"★".repeat(stars)}</span>}
          </h4>
          <Meta items={[str(o, "area"), str(o, "cheapest_room_type"), str(o, "cheapest_board")]} />
          {cancel && (
            <p className={`mt-1 text-xs ${free ? "text-emerald-600 dark:text-emerald-400" : "text-fg-muted"}`}>
              {free ? "✓ " : ""}
              {cancel}
            </p>
          )}
        </div>
        <Price
          value={inr(num(o, "price_inr"))}
          note={nights ? `${nights} nights${perNight ? ` · ${perNight}/night` : ""}` : null}
        />
      </div>
    </article>
  );
}

/** Transfer tiers, as the supplier priced them for this party. */
function Transfers({ o }: { o: OptionItem }) {
  const tiers = o.transfer_prices;
  if (!Array.isArray(tiers) || !tiers.length) return null;

  // Every figure is the TOTAL for `priced_for_pax` people, never a per-head
  // unit -- saying so on the card is what stops a reader multiplying it by the
  // party size, which is exactly how a 7-person quote came out 7x too high.
  const pax = tiers.map((t) => num(t as OptionItem, "priced_for_pax")).find((n) => n != null);

  return (
    <div className="mt-2 rounded-lg bg-surface-2 px-2.5 py-2">
      <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-fg-muted">
        Transfer {pax && pax > 1 ? `· total for ${pax} travellers` : "· per booking"}
      </p>
      <ul className="space-y-0.5">
        {tiers.map((t, i) => {
          const tier = t as OptionItem;
          const label = str(tier, "transfer_type") ?? "Transfer";
          const price = str(tier, "price_display") ?? inr(num(tier, "price_inr"));
          const free = num(tier, "price_inr") === 0;
          return (
            <li key={i} className="flex items-baseline justify-between gap-3 text-xs">
              <span className="min-w-0 truncate text-fg-muted">{label}</span>
              <span className={`shrink-0 tabular-nums ${free ? "text-emerald-600 dark:text-emerald-400" : "font-medium"}`}>
                {price}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function TourCard({ o }: { o: OptionItem }) {
  const name = str(o, "name") ?? "Tour";
  const rating = num(o, "rating");
  const reviews = num(o, "reviews_count");
  const desc = str(o, "short_description");
  const addon = o.is_addon === true;

  // The server decides how a price reads: a "Per Group up to 6 Guests" add-on
  // is NOT per adult, and labelling it so understated it badly. Prefer the
  // server's own `price_display`, and only fall back to a computed per-adult
  // figure when the row does not carry one.
  const tourId = num(o, "tour_id");
  const optionId = num(o, "option_id") ?? str(o, "option_id");
  const supplierId = num(o, "supplier_id");

  const display = str(o, "price_display");
  const basisNote =
    str(o, "price_basis") === "per_group" ? "priced per group" : "per adult";

  return (
    <article className="overflow-hidden rounded-xl border border-border bg-bg">
      <SafeImage src={firstImage(o)} alt={name} ratio="aspect-[16/9]" className="rounded-none" />
      <div className="flex gap-3 p-3">
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-medium">
            {addon && (
              <span className="mr-1.5 rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium text-fg-muted">
                Add-on
              </span>
            )}
            {name}
            {o.is_recommended === true && (
              <span className="ml-1.5 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium text-accent">
                Recommended
              </span>
            )}
          </h4>
          <Meta
            items={[
              str(o, "category"),
              str(o, "duration"),
              rating != null ? `★ ${rating.toFixed(1)}${reviews ? ` (${reviews})` : ""}` : null,
            ]}
          />
          {desc && <p className="mt-1.5 line-clamp-2 text-xs text-fg-muted">{desc}</p>}
          {addon && (
            <p className="mt-1 text-[11px] text-fg-muted">Needs a main option to book</p>
          )}
        </div>
        {display ? (
          <div className="shrink-0 text-right">
            <div className="text-base font-semibold tabular-nums">{display}</div>
          </div>
        ) : (
          <Price value={inr(num(o, "price_per_adult_inr"))} note={basisNote} />
        )}
      </div>
      <div className="px-3 pb-3">
        {/* Only when the row carries the supplier ids -- search rows do not,
            variant rows do, and a link that cannot fetch is worse than none. */}
        {tourId != null && optionId != null && supplierId != null && (
          <div className="mb-2">
            <TourDetails
              tourId={tourId}
              optionId={optionId}
              supplierId={supplierId}
              name={name}
            />
          </div>
        )}
        <Transfers o={o} />
        {str(o, "price_note") && (
          <p className="mt-2 text-[11px] text-amber-700 dark:text-amber-500">
            {str(o, "price_note")}
          </p>
        )}
      </div>
    </article>
  );
}

/** A dish the card can show: name, price and the supplier's own photo. */
interface DishPhoto {
  name: string;
  price: string | null;
  image: string | null;
}

function dishPhotos(o: OptionItem): DishPhoto[] {
  const raw = o.dish_photos;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((d) => d as OptionItem)
    .map((d) => ({ name: str(d, "name") ?? "", price: str(d, "price_display"), image: str(d, "image_url") }))
    .filter((d) => d.name && d.image);
}

/** The menu's food photos, scrollable sideways under the restaurant. */
function DishStrip({ dishes }: { dishes: DishPhoto[] }) {
  if (!dishes.length) return null;
  return (
    <div className="mt-2">
      <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-fg-muted">On the menu</p>
      <ul className="ui-list flex snap-x gap-2 overflow-x-auto pb-1">
        {dishes.map((d, i) => (
          <li key={`${d.name}-${i}`} className="w-24 shrink-0 snap-start">
            <SafeImage src={d.image} alt={d.name} ratio="aspect-square" />
            <p className="mt-1 line-clamp-2 text-[11px] leading-tight">{d.name}</p>
            {d.price && <p className="text-[11px] font-medium tabular-nums">{d.price}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RestaurantCard({ o }: { o: OptionItem }) {
  const name = str(o, "name") ?? "Restaurant";
  const km = num(o, "distance_km");
  const from = str(o, "distance_from");
  const tags = Array.isArray(o.tags) ? (o.tags as unknown[]).filter((t): t is string => typeof t === "string") : [];
  const day = o.hours_on_date as OptionItem | undefined;
  const dayHours =
    day && typeof day === "object"
      ? day.closed === true
        ? `${str(day, "weekday") ?? ""}: closed`
        : str(day, "opening_hours") && `${str(day, "weekday") ?? ""}: ${str(day, "opening_hours")}`
      : null;

  return (
    <article className="overflow-hidden rounded-xl border border-border bg-bg">
      <SafeImage src={firstImage(o)} alt={name} ratio="aspect-[16/9]" className="rounded-none" />
      <div className="p-3">
        <div className="flex gap-3">
          <div className="min-w-0 flex-1">
            <h4 className="text-sm font-medium">{name}</h4>
            <Meta
              items={[
                str(o, "rating_display") ? `★ ${str(o, "rating_display")}` : null,
                str(o, "area"),
                // Straight-line distance, said as such: there is no route data.
                km != null ? `${km} km${from ? ` from ${from}` : ""}` : null,
              ]}
            />
          </div>
          {/* The supplier's starting price is its cheapest menu item. */}
          <Price value={str(o, "price_from_display")} note={str(o, "menu_price_range_display")} />
        </div>
        {tags.length > 0 && (
          <ul className="ui-list mt-2 flex flex-wrap gap-1">
            {tags.slice(0, 4).map((t) => (
              <li key={t} className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-fg-muted">{t}</li>
            ))}
            {tags.length > 4 && <li className="px-1 py-0.5 text-[10px] text-fg-muted">+{tags.length - 4}</li>}
          </ul>
        )}
        {dayHours && <p className="mt-1.5 text-[11px] text-fg-muted">Published hours · {dayHours}</p>}
        <DishStrip dishes={dishPhotos(o)} />
      </div>
    </article>
  );
}

function FlightCard({ o }: { o: OptionItem }) {
  const stops = num(o, "stops");
  const mins = num(o, "duration_min");
  const dur = mins ? `${Math.floor(mins / 60)}h ${mins % 60}m` : null;

  return (
    <article className="flex gap-3 rounded-xl border border-border bg-bg p-3">
      <div className="min-w-0 flex-1">
        <h4 className="truncate text-sm font-medium">{str(o, "airline") ?? "Flight"}</h4>
        <Meta
          items={[
            str(o, "route_outbound"),
            str(o, "route_return"),
            stops != null ? (stops === 0 ? "Non-stop" : `${stops} stop${stops > 1 ? "s" : ""}`) : null,
            dur,
            o.refundable === true ? "✓ Refundable" : null,
          ]}
        />
      </div>
      <Price value={inr(num(o, "price_inr"))} note="all pax" />
    </article>
  );
}

const LABELS: Record<string, string> = {
  hotels: "Hotels",
  tours: "Tours",
  flights: "Flights",
  restaurants: "Restaurants",
};

const CARDS: Record<string, ({ o }: { o: OptionItem }) => React.JSX.Element> = {
  hotels: HotelCard,
  flights: FlightCard,
  restaurants: RestaurantCard,
};

/** Photo-led cards scroll sideways instead of stacking into a wall. */
const CAROUSEL_KINDS = new Set(["tours", "restaurants"]);

export function OptionCards({ group }: { group: OptionGroup }) {
  if (!group.items?.length) return null;

  const Card = CARDS[group.kind] ?? TourCard;
  const isTours = CAROUSEL_KINDS.has(group.kind);

  return (
    <section className="mt-3">
      <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">
        {LABELS[group.kind] ?? group.kind} · {group.items.length}
      </h3>

      {isTours ? (
        <Carousel count={group.items.length}>
          {group.items.map((item, i) => (
            // basis-full on phones, half the row from `sm` up: two cards
            // visible at a time, as asked.
            <div key={i} className="w-full shrink-0 snap-start sm:w-[calc(50%-0.375rem)]">
              <Card o={item} />
            </div>
          ))}
        </Carousel>
      ) : (
        <div className="flex flex-col gap-2">
          {group.items.map((item, i) => (
            <Card key={i} o={item} />
          ))}
        </div>
      )}
    </section>
  );
}
