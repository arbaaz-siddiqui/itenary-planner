// The UI half of the contract check.
//
// The Python suite proves the fixtures satisfy the models and that this file
// mirrors them field-for-field. This suite proves the fixtures are actually
// USABLE by a card renderer: that every row carries what a card needs, and
// that the one shared price formatter says the right thing for every basis.
//
// `priceLine` gets the most attention here because it is the single place a
// price is turned into words. The most expensive mistake this product can make
// is printing a supplier total as if it were a per-person rate — a private
// desert-safari tier is ₹11,569 for a vehicle carrying up to six, and reading
// that as per-head turns a ₹11,569 quote into ₹69,414.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { MAX_INLINE_ROWS, isEmpty, priceLine } from "./contract";
import type { Kind, PriceBasis, ToolResult } from "./contract";

const KINDS: Kind[] = [
  "flights",
  "hotels",
  "tours",
  "transfers",
  "restaurants",
  "visa",
  "packages",
];

function fixture(kind: Kind): ToolResult {
  const path = fileURLToPath(
    new URL(`../../../fixtures/contract/${kind}.json`, import.meta.url),
  );
  return JSON.parse(readFileSync(path, "utf-8")) as ToolResult;
}

describe("fixtures", () => {
  it.each(KINDS)("%s parses and declares its own kind", (kind) => {
    const r = fixture(kind);
    expect(r.kind).toBe(kind);
    expect(r.ref).toBeTruthy();
    expect(r.rows.length).toBeGreaterThan(0);
    expect(r.rows.length).toBeLessThanOrEqual(MAX_INLINE_ROWS);
  });

  it.each(KINDS)("%s rows can each render a card", (kind) => {
    for (const row of fixture(kind).rows) {
      // Clickable.
      expect(row.id).toBeTruthy();
      expect(row.detail_ref).toBeTruthy();
      // Readable.
      expect(row.title.trim()).not.toBe("");
      // Honest: a price always arrives with a basis and a party size.
      if (row.price_inr !== null) {
        expect(row.priced_for_pax).toBeGreaterThanOrEqual(1);
        expect(priceLine(row.price_inr, row.price_basis, row.priced_for_pax)).not.toBe("");
      }
      // Images reserve space before they load, so the deck never reflows.
      if (row.image) {
        expect(row.image.ratio).toBeGreaterThan(0);
        expect(row.image.src.startsWith("/api/img")).toBe(true);
      }
    }
  });

  it.each(KINDS)("%s facets are pre-formatted strings", (kind) => {
    for (const row of fixture(kind).rows) {
      for (const [label, value] of Object.entries(row.facets)) {
        expect(typeof value, `${kind}.${row.id}.${label}`).toBe("string");
      }
    }
  });

  it("a degraded fixture carries the sentence to show the customer", () => {
    const degraded = KINDS.map(fixture).filter((r) => r.degraded !== null);
    expect(degraded.length).toBeGreaterThan(0);
    for (const r of degraded) {
      expect(r.degraded!.disclosure.trim()).not.toBe("");
    }
  });

  it("suggestions carry an action the UI can offer", () => {
    const withSuggestions = KINDS.map(fixture).filter((r) => r.suggest.length > 0);
    expect(withSuggestions.length).toBeGreaterThan(0);
    for (const r of withSuggestions) {
      expect(r.suggest.length).toBeLessThanOrEqual(3);
      for (const s of r.suggest) expect(s.text.trim()).not.toBe("");
    }
  });
});

describe("priceLine", () => {
  const cases: [number | null, PriceBasis, number, string][] = [
    [1793, "per_adult", 2, "₹1,793 per adult"],
    [15500, "per_room_night", 2, "₹15,500 per room, per night"],
    [11569, "per_vehicle", 6, "₹11,569 per vehicle"],
    [11569, "per_group", 6, "₹11,569 for the group of 6"],
    [38400, "total", 2, "₹38,400 total for 2"],
    [38400, "total", 1, "₹38,400"],
    [null, "total", 2, "On request"],
    [4180, "on_request", 2, "On request"],
  ];

  it.each(cases)("%s / %s / %s pax", (price, basis, pax, expected) => {
    expect(priceLine(price, basis, pax)).toBe(expected);
  });

  it("never renders a bare number for a vehicle price", () => {
    // The regression this guards: a private transfer tier printed as "₹11,569"
    // with no basis, which a reader multiplies by the party size.
    const line = priceLine(11569, "per_vehicle", 6);
    expect(line).not.toBe("₹11,569");
    expect(line).toContain("vehicle");
  });

  it("uses Indian digit grouping", () => {
    expect(priceLine(127600, "total", 1)).toBe("₹1,27,600");
  });
});

describe("isEmpty", () => {
  const base: ToolResult = {
    ref: "ref_x",
    kind: "hotels",
    summary_md: "",
    rows: [],
    facts: {},
    more: null,
    degraded: null,
    suggest: [],
    error: null,
  };

  it("is true when there is nothing to show and nothing to explain", () => {
    expect(isEmpty(base)).toBe(true);
  });

  it("is false when there is an error, because that needs saying", () => {
    expect(isEmpty({ ...base, error: "Supplier returned 503" })).toBe(false);
  });
});
