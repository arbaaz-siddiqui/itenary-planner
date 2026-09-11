import { describe, expect, it } from "vitest";
import type { Element } from "hast";
import { findPriceIndex, looksNumeric, parseResultTable, splitParts } from "./resultTable";

// Hand-built hast, shaped exactly like what remark-gfm hands react-markdown:
// table > thead > tr > th, table > tbody > tr > td.
const el = (tagName: string, children: Element["children"]): Element => ({
  type: "element",
  tagName,
  properties: {},
  children,
});

const cell = (tag: string, text: string): Element =>
  el(tag, [{ type: "text", value: text }]);

function table(headers: string[], rows: string[][]): Element {
  return el("table", [
    el("thead", [el("tr", headers.map((h) => cell("th", h)))]),
    el("tbody", rows.map((r) => el("tr", r.map((c) => cell("td", c))))),
  ]);
}

const TOUR_HEADERS = [
  "Tour",
  "Price/adult",
  "Type",
  "Duration",
  "Transfer",
  "Cancellation",
  "Start times",
];

const TOUR_ROW = [
  "⭐ Premium Red Dune Desert Safari: BBQ Dinner, Belly Dance & Camel Ride",
  "₹3,661",
  "Shared",
  "6h 30m",
  "Sharing Transfer: ₹1,793 · Without Transfer: Included (₹0) · Private Transfer: ₹11,452",
  "Free until 24h before",
  "14:30, 15:00",
];

describe("findPriceIndex", () => {
  it("matches the headers the agent actually emits", () => {
    expect(findPriceIndex(["Tour", "Price/adult"])).toBe(1);
    expect(findPriceIndex(["Hotel", "Board", "Total (5N)"])).toBe(2);
    expect(findPriceIndex(["Flight", "Fare"])).toBe(1);
    expect(findPriceIndex(["Room", "Rate"])).toBe(1);
  });

  it("does not match a prose table", () => {
    expect(findPriceIndex(["Term", "Meaning"])).toBe(-1);
    expect(findPriceIndex(["Day", "Plan"])).toBe(-1);
  });
});

describe("parseResultTable", () => {
  it("pulls the name and the price out of a 7-column tour table", () => {
    const parsed = parseResultTable(table(TOUR_HEADERS, [TOUR_ROW]));
    expect(parsed).not.toBeNull();
    expect(parsed!.priceIndex).toBe(1);

    const row = parsed!.rows[0];
    expect(row.price).toEqual({ label: "Price/adult", text: "₹3,661" });
    // The star is a badge, not part of the name.
    expect(row.recommended).toBe(true);
    expect(row.name).not.toContain("⭐");
    expect(row.name).toBe(
      "Premium Red Dune Desert Safari: BBQ Dinner, Belly Dance & Camel Ride",
    );
  });

  it("keeps every non-name, non-price column as a labelled attribute", () => {
    const parsed = parseResultTable(table(TOUR_HEADERS, [TOUR_ROW]));
    expect(parsed!.rows[0].rest.map((c) => c.label)).toEqual([
      "Type",
      "Duration",
      "Transfer",
      "Cancellation",
      "Start times",
    ]);
  });

  it("drops empty and placeholder cells rather than rendering blank rows", () => {
    const parsed = parseResultTable(
      table(["Tour", "Price", "Type", "Duration"], [["A", "₹1", "—", ""]]),
    );
    expect(parsed!.rows[0].rest).toEqual([]);
  });

  it("marks only the starred rows — the /g regex must not leak lastIndex", () => {
    const parsed = parseResultTable(
      table(
        ["Tour", "Price"],
        [
          ["⭐ First", "₹1"],
          ["⭐ Second", "₹2"],
          ["Third", "₹3"],
          ["⭐ Fourth", "₹4"],
        ],
      ),
    );
    expect(parsed!.rows.map((r) => r.recommended)).toEqual([true, true, false, true]);
  });

  it("returns null for an incidental prose table", () => {
    expect(parseResultTable(table(["Term", "Meaning"], [["B&B", "Breakfast"]]))).toBeNull();
  });

  it("returns null for a table with no body rows", () => {
    expect(parseResultTable(table(["Tour", "Price"], []))).toBeNull();
  });

  it("returns null for a single-column table", () => {
    expect(parseResultTable(table(["Price"], [["₹1"]]))).toBeNull();
  });

  it("returns null for undefined and for non-table elements", () => {
    expect(parseResultTable(undefined)).toBeNull();
    expect(parseResultTable(el("div", []))).toBeNull();
  });

  it("reads rows hung straight off the table with no tbody", () => {
    const t = el("table", [
      el("thead", [el("tr", [cell("th", "Tour"), cell("th", "Price")])]),
      el("tr", [cell("td", "A"), cell("td", "₹1")]),
    ]);
    const parsed = parseResultTable(t);
    expect(parsed!.rows).toHaveLength(1);
    expect(parsed!.rows[0].name).toBe("A");
  });

  it("reads through inline markup — a bolded price is still the price", () => {
    const t = el("table", [
      el("thead", [el("tr", [cell("th", "Hotel"), cell("th", "Total")])]),
      el("tbody", [
        el("tr", [cell("td", "Rove"), el("td", [el("strong", [{ type: "text", value: "₹68,400" }])])]),
      ]),
    ]);
    expect(parseResultTable(t)!.rows[0].price?.text).toBe("₹68,400");
  });
});

describe("splitParts", () => {
  it("breaks a run-on transfer string into readable lines", () => {
    expect(
      splitParts("Sharing Transfer: ₹1,793 · Without Transfer: Included (₹0) · Private: ₹11,452"),
    ).toEqual([
      "Sharing Transfer: ₹1,793",
      "Without Transfer: Included (₹0)",
      "Private: ₹11,452",
    ]);
  });

  it("leaves a value with no separator as one part", () => {
    expect(splitParts("Free until 24h before")).toEqual(["Free until 24h before"]);
  });

  it("never yields empty parts", () => {
    expect(splitParts(" · A ·· B · ")).toEqual(["A", "B"]);
  });
});

describe("looksNumeric", () => {
  it("is true for figures and false for prose", () => {
    expect(looksNumeric("₹3,661")).toBe(true);
    expect(looksNumeric("6h 30m")).toBe(false);
    expect(looksNumeric("Free until 24h before")).toBe(false);
    expect(looksNumeric("Shared")).toBe(false);
  });
});
