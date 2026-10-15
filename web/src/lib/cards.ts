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
