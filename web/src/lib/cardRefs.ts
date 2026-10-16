import type { CardRef } from "./cardActions";
import { idOf, kindOf } from "./cards";
import type { OptionItem } from "./types";

/** Key under which a message tags an item with its group's kind. */
export const KIND_TAG = "__kind";

/** One flight at one fare: airline, each leg's flight numbers and time, price. */
function flightSignature(item: OptionItem): string | null {
  const leg = (k: string) => {
    const l = item[k];
    if (!l || typeof l !== "object") return "";
    const o = l as OptionItem;
    return `${JSON.stringify(o.flight_numbers ?? [])}@${o.departure_time ?? ""}`;
  };
  const out = leg("outbound");
  if (!out) return null;
  return `${item.airline ?? ""}|${out}|${leg("inbound")}|${item.price_inr ?? ""}`;
}

/**
 * Items to card refs: kind from the group (or the item's own tag), a stable
 * id, and no duplicates. Two rows naming the same result render once.
 */
export function toCardRefs(items: OptionItem[], groupKind?: string): CardRef[] {
  const seen = new Set<string>();
  const sameTrip = new Set<string>();
  const out: CardRef[] = [];
  for (const item of items) {
    const tag = typeof item[KIND_TAG] === "string" ? (item[KIND_TAG] as string) : undefined;
    const kind = kindOf(item, groupKind ?? tag);
    // The same flight at the same fare, sold by two suppliers, is one choice
    // for the customer: show it once.
    if (kind === "flight") {
      const trip = flightSignature(item);
      if (trip && sameTrip.has(trip)) continue;
      if (trip) sameTrip.add(trip);
    }
    let id = idOf(item, kind);
    if (seen.has(id)) id = `${id}-${out.length}`;
    seen.add(id);
    out.push({ id, kind, item });
  }
  return out;
}
