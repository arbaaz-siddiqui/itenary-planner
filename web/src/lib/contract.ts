// contract — the TypeScript mirror of `contracts.py`.
//
// Every card, drawer and Ask-AI panel renders from these types. Nothing in the
// UI may parse the agent's prose to discover a result; if a card needs a fact,
// the fact belongs in a field here and is filled in where the supplier data is
// known.
//
// Kept in step with the Python side by tests/test_contracts.py, which reads
// this file and fails when a field exists on one side only. If you add a field,
// add it in both places and update the fixtures.

// ---------------------------------------------------------------- vocabulary

/** Product families we sell. One card shape per kind. */
export type Kind =
  | "flights"
  | "hotels"
  | "tours"
  | "transfers"
  | "restaurants"
  | "visa"
  | "packages";

/**
 * What a price is FOR. Never render a price without rendering this too --
 * "₹11,569" and "₹11,569 for the vehicle, up to 6 travellers" are different
 * claims, and confusing them is how a quote comes out six times too high.
 */
export type PriceBasis =
  | "per_adult"
  | "per_group"
  | "per_room_night"
  | "per_vehicle"
  | "total"
  | "on_request";

/** Badge role. The theme picks the colour; the data picks the role. */
export type Tone = "neutral" | "ok" | "warn" | "danger";

export type SuggestionReason =
  | "adjacent"
  | "cheaper"
  | "upgrade"
  | "bundle"
  | "gap"
  | "constraint";

/** Anything other than live must be disclosed to the customer. */
export type DegradedReason = "cached" | "stale" | "partial" | "empty" | "unavailable";

// ----------------------------------------------------------------- row parts

export interface Badge {
  label: string;
  tone: Tone;
}

/**
 * A card image. `src` is always our own origin (`/api/img?src=...`), never the
 * supplier's — their hosts are unknown ahead of time and some 404. `ratio`
 * lets the card reserve space so the deck does not reflow as images land.
 */
export interface ImageRef {
  src: string;
  alt: string;
  ratio: number;
}

export interface Row {
  id: string;
  kind: Kind;
  title: string;
  subtitle: string;

  price_inr: number | null;
  price_basis: PriceBasis;
  /** Party size these figures were quoted for. Never multiply by it. */
  priced_for_pax: number;

  badges: Badge[];
  image: ImageRef | null;

  /** Handle for the detail drawer and for Ask AI. */
  detail_ref: string;

  /** Already-formatted display pairs. Render as given; do not reformat. */
  facets: Record<string, string>;
}

// ------------------------------------------------------------ envelope parts

export interface MoreInfo {
  total_available: number;
  showing: number;
  next_offset: number | null;
}

export interface Degraded {
  reason: DegradedReason;
  stale_seconds: number | null;
  /** Plain sentence to show the customer. Do not paraphrase it. */
  disclosure: string;
  missing: string[];
}

export interface Suggestion {
  kind: Kind;
  reason: SuggestionReason;
  text: string;
  action: string;
}

// ----------------------------------------------------------------- envelope

/** Inline rows are capped; the rest come from the artifact by `ref`. */
export const MAX_INLINE_ROWS = 5;

export interface ToolResult {
  ref: string;
  kind: Kind;
  summary_md: string;
  rows: Row[];
  facts: Record<string, unknown>;
  more: MoreInfo | null;
  degraded: Degraded | null;
  suggest: Suggestion[];
  error: string | null;
}

// -------------------------------------------------------------------- detail

export interface DetailSection {
  heading: string;
  pairs: [string, string][];
  body: string[];
  table: Record<string, string>[];
}

export interface ItemDetail {
  id: string;
  kind: Kind;
  title: string;
  subtitle: string;
  images: ImageRef[];
  price_inr: number | null;
  price_basis: PriceBasis;
  priced_for_pax: number;
  badges: Badge[];
  sections: DetailSection[];
  facts: Record<string, unknown>;
  degraded: Degraded | null;
  error: string | null;
}

// ------------------------------------------------------------------- helpers

/**
 * The price line for a card, basis included.
 *
 * Centralised because every card must say the same thing about the same
 * figure. A tour's private tier is one price for the vehicle; a sharing tier is
 * one price per traveller; a hotel rate is per room per night. Rendering the
 * number without the basis is the single most expensive mistake this UI can
 * make, so there is exactly one function that does it.
 */
export function priceLine(
  price: number | null,
  basis: PriceBasis,
  pax: number,
): string {
  if (price == null || basis === "on_request") return "On request";
  const amount = `₹${Math.round(price).toLocaleString("en-IN")}`;
  switch (basis) {
    case "per_adult":
      return `${amount} per adult`;
    case "per_room_night":
      return `${amount} per room, per night`;
    case "per_vehicle":
      return `${amount} per vehicle`;
    case "per_group":
      return `${amount} for the group of ${pax}`;
    case "total":
      return pax > 1 ? `${amount} total for ${pax}` : amount;
  }
}

/** True when the result carries nothing to show and no error to explain. */
export function isEmpty(r: ToolResult): boolean {
  return r.rows.length === 0 && r.error == null;
}
