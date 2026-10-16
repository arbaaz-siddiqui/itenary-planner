import type { OptionItem } from "./types";

/** The seven card types the agent can return. */
export type CardKind = "restaurant" | "flight" | "hotel" | "tour" | "package" | "transfer" | "visa";

/** Option-group kinds from the API (mostly plural) mapped to a card type. */
const GROUP_KIND: Record<string, CardKind> = {
  restaurants: "restaurant",
  restaurant: "restaurant",
  flights: "flight",
  flight: "flight",
  hotels: "hotel",
  hotel: "hotel",
  tours: "tour",
  tour: "tour",
  packages: "package",
  package: "package",
  transfers: "transfer",
  transfer: "transfer",
  visa: "visa",
  visas: "visa",
};

/**
 * Card type for one item. The group's kind wins; without one (a table row
 * matched by name), the item's own identifying keys decide.
 */
export function kindOf(item: OptionItem, groupKind?: string | null): CardKind {
  const fromGroup = groupKind ? GROUP_KIND[groupKind] : undefined;
  if (fromGroup) return fromGroup;
  if (item.restaurant_id != null) return "restaurant";
  if (item.hotel_id != null || typeof item.hotel_name === "string") return "hotel";
  if (item.outbound != null || item.fare_source_code != null || typeof item.airline === "string") return "flight";
  if (item.package_id != null) return "package";
  if (item.vehicle_name != null || item.transfer_id != null) return "transfer";
  if (item.visa_id != null || item.visa_type != null) return "visa";
  return "tour";
}

// --- defensive field readers: supplier payloads vary, a missing key must
// never blank or break a card ---------------------------------------------

export const str = (o: OptionItem | null | undefined, k: string): string | null => {
  const v = o?.[k];
  return typeof v === "string" && v.trim() ? v.trim() : null;
};

export const num = (o: OptionItem | null | undefined, k: string): number | null => {
  const v = o?.[k];
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() && Number.isFinite(Number(v))) return Number(v);
  return null;
};

export const strList = (o: OptionItem | null | undefined, k: string): string[] => {
  const v = o?.[k];
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.trim() !== "") : [];
};

export const objList = (o: OptionItem | null | undefined, k: string): OptionItem[] => {
  const v = o?.[k];
  return Array.isArray(v) ? v.filter((x): x is OptionItem => !!x && typeof x === "object") : [];
};

export const obj = (o: OptionItem | null | undefined, k: string): OptionItem | null => {
  const v = o?.[k];
  return v && typeof v === "object" && !Array.isArray(v) ? (v as OptionItem) : null;
};

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

/** ₹2,541.95 / ₹13,290.34 / ₹340 — en-IN grouping, paise only when present. */
export function formatInr(n: number | null | undefined): string | null {
  if (n == null || !Number.isFinite(n)) return null;
  return INR.format(n);
}

/** All the photos an item carries, primary first, without repeats. */
export function imagesOf(item: OptionItem): string[] {
  const out: string[] = [];
  const push = (v: unknown) => {
    if (typeof v === "string" && v.trim() && !out.includes(v)) out.push(v);
  };
  push(item.image_url);
  for (const v of Array.isArray(item.image_urls) ? item.image_urls : []) push(v);
  return out;
}

/** The photos a detail panel shows: the full gallery once loaded (it already
 *  holds the cover, sometimes under another host), else the card's own. */
export function mergeImages(cardImages: string[], gallery: string[]): string[] {
  const out: string[] = [];
  for (const v of gallery.length ? gallery : cardImages) {
    if (typeof v === "string" && v.trim() && !out.includes(v)) out.push(v);
  }
  return out;
}

/** The name a card shows, whatever key this kind uses for it. */
export function titleOf(item: OptionItem, kind: CardKind): string {
  switch (kind) {
    case "hotel":
      return str(item, "hotel_name") ?? str(item, "name") ?? "Hotel";
    case "flight":
      return str(item, "airline") ?? "Flight";
    case "transfer":
      return str(item, "vehicle_name") ?? "Transfer";
    case "visa":
      return str(item, "visa_type") ?? "Visa";
    default:
      return str(item, "name") ?? (kind === "restaurant" ? "Restaurant" : kind === "package" ? "Package" : "Tour");
  }
}

/**
 * A stable id for saving and for the detail URL. Supplier ids where they
 * exist; otherwise a slug of the title, which is unique enough within a reply.
 */
export function idOf(item: OptionItem, kind: CardKind): string {
  const key =
    item.restaurant_id ?? item.hotel_id ?? item.package_id ?? item.visa_id ??
    (item.tour_id != null ? `${item.tour_id}${item.option_id != null ? `-${item.option_id}` : ""}` : null) ??
    item.unique_key ?? item.fare_source_code;
  if (key != null && String(key).length <= 64) return `${kind}-${key}`;
  const slug = titleOf(item, kind).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const price = num(item, "price_inr") ?? num(item, "price_per_adult_inr") ?? "";
  return `${kind}-${slug}-${price}`.slice(0, 96);
}

export const KIND_LABEL: Record<CardKind, string> = {
  restaurant: "Restaurants",
  flight: "Flights",
  hotel: "Hotels",
  tour: "Tours",
  package: "Holiday packages",
  transfer: "Transfers",
  visa: "Visas",
};

/**
 * Key a result table row's own price is copied under when that row becomes a
 * card. The agent's table says what the customer's party pays ("₹17,713.08"
 * for 2 adults + 1 child) while the card shows the per-person price; without
 * this the party total vanished from the screen once the row turned into a
 * card.
 */
export const PARTY_TOTAL_TAG = "__party_total";

/** "₹17,713.08 for your party", or null when the row carried no total. */
export function partyTotalNote(item: OptionItem): string | null {
  const v = item[PARTY_TOTAL_TAG];
  return typeof v === "string" && v.trim() ? `${v.trim()} for your party` : null;
}

// Per-unit prices a card already shows. A row price equal to one of these is
// the same number, not a party total.
const UNIT_PRICE_KEYS = ["price_per_person_inr", "price_per_adult_inr", "per_night_inr", "price_inr"];

/**
 * Carry the table row's own price onto the card when it is a different number
 * from what the card shows (the party total), so it is not lost.
 */
export function withRowTotal(item: OptionItem, rowPrice: string | null | undefined): OptionItem {
  const amount = Number(String(rowPrice ?? "").replace(/[^0-9.]/g, ""));
  if (!(amount > 0)) return item;
  const same = UNIT_PRICE_KEYS.some((k) => typeof item[k] === "number" && Math.abs((item[k] as number) - amount) < 1);
  if (same) return item;
  const shown = String(rowPrice).match(/₹\s?[\d,]+(?:\.\d+)?/)?.[0] ?? `₹${amount}`;
  return { ...item, [PARTY_TOTAL_TAG]: shown };
}

/** What a package card's price line says. Per person on the card, the party
 *  total underneath (the reply quotes the total). A package the list did not
 *  price says where its price is, never "Rs 0". */
export function packagePrice(o: OptionItem): { value: string; label: string | null; note: string | null } {
  const perPerson = num(o, "price_per_person_inr");
  const total = num(o, "price_inr");
  if (perPerson && total) {
    return { value: formatInr(perPerson) ?? "", label: "Per person", note: `${formatInr(total)} total` };
  }
  if (total) return { value: formatInr(total) ?? "", label: "Total", note: null };
  if (o.pricing_available === false) return { value: "Price on request", label: null, note: null };
  return { value: "See price", label: null, note: "Open the package for its live price" };
}
