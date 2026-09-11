// Turning the agent's GFM comparison tables into something a customer can
// actually read.
//
// The agent answers tour/hotel questions with 7-column markdown tables:
//
//   | Tour | Price/adult | Type | Duration | Transfer | Cancellation | Start |
//
// Rendered as a real <table> those are far wider than the chat bubble, so the
// name column and the price column can never be on screen at the same time —
// the two facts the customer is comparing. This module extracts the table into
// plain data so the renderer can show cards instead.
//
// Deliberately pure (hast in, plain objects out) so it is unit-testable with
// no DOM and cannot itself break streaming.

import type { Element, ElementContent, Parents, RootContent } from "hast";

/** A parsed cell. `raw` keeps the markdown-rendered text for display. */
export interface ResultCell {
  /** Column header this cell belongs to, verbatim. */
  label: string;
  /** Cell text with the recommended star stripped out. */
  text: string;
}

export interface ResultRow {
  /** First column — the thing being compared (tour name, hotel name). */
  name: string;
  /** True when the row was marked with ⭐ (the agent's "recommended" marker). */
  recommended: boolean;
  /** The price cell, if a price-ish column exists. */
  price: ResultCell | null;
  /** Everything that is neither the name nor the price, in table order. */
  rest: ResultCell[];
}

export interface ResultTable {
  headers: string[];
  rows: ResultRow[];
  /** Index into `headers` of the column treated as the price. */
  priceIndex: number;
}

/** A header naming the number customers compare on. */
const PRICE_RE = /price|fare|total|rate/i;

/** The agent's "recommended" marker, plus the variation-selector form. */
const STAR_RE = /⭐️?/gu;

/**
 * Long attribute values arrive as one run-on string:
 *   "Sharing Transfer: ₹1,793 · Without Transfer: Included (₹0) · Private: ₹11,452"
 * Splitting on the middot is what makes them readable as separate lines.
 * Also accepts the bullet "•" and a semicolon, which the agent sometimes uses.
 */
const PART_SEPARATOR = /\s*[·•;]\s*/;

/** Flatten a hast subtree to its text content. */
function textOf(node: RootContent | ElementContent | Parents): string {
  if (node.type === "text") return node.value;
  if (node.type === "comment" || node.type === "doctype") return "";
  const kids = (node as Parents).children ?? [];
  let out = "";
  for (const k of kids) out += textOf(k);
  return out;
}

/** Direct element children with one of the given tag names. */
function childElements(node: Parents | undefined, ...tags: string[]): Element[] {
  if (!node) return [];
  const out: Element[] = [];
  for (const c of node.children) {
    if (c.type === "element" && tags.includes(c.tagName)) out.push(c);
  }
  return out;
}

/** Cell text, normalised: collapsed whitespace, star marker removed. */
function cellText(cell: Element): string {
  return textOf(cell).replace(STAR_RE, "").replace(/\s+/g, " ").trim();
}

/**
 * Does this table compare results (prices), or is it incidental prose?
 *
 * Only a header matching /price|fare|total|rate/i qualifies. A two-column
 * "Term | Meaning" glossary the agent sometimes writes must keep rendering as
 * an ordinary table, so this must not fire on it.
 */
export function findPriceIndex(headers: string[]): number {
  return headers.findIndex((h) => PRICE_RE.test(h));
}

/**
 * Parse a hast <table> element into comparison data.
 *
 * Returns null when the table is not a result comparison — no header row, no
 * body rows, fewer than two columns, or no price-ish header. The caller then
 * falls back to the plain table renderer.
 */
export function parseResultTable(table: Element | undefined): ResultTable | null {
  if (!table || table.tagName !== "table") return null;

  // GFM always emits thead + tbody, but read defensively: some rewriters drop
  // the tbody wrapper and hang <tr> straight off the table.
  const thead = childElements(table, "thead")[0];
  const tbodies = childElements(table, "tbody");
  const headerRow = childElements(thead, "tr")[0];
  if (!headerRow) return null;

  const headers = childElements(headerRow, "th", "td").map(cellText);
  if (headers.length < 2) return null;

  const priceIndex = findPriceIndex(headers);
  if (priceIndex < 0) return null;

  const bodyRows: Element[] = tbodies.length
    ? tbodies.flatMap((b) => childElements(b, "tr"))
    : childElements(table, "tr").filter((r) => r !== headerRow);
  if (!bodyRows.length) return null;

  const rows: ResultRow[] = [];
  for (const tr of bodyRows) {
    const cells = childElements(tr, "td", "th");
    if (!cells.length) continue;

    // The star can sit in any cell of the row, but in practice the agent puts
    // it in the first. Scan the whole row so a trailing marker still counts.
    const recommended = cells.some((c) => STAR_RE.test(textOf(c)));
    // Regexes with /g carry lastIndex between .test() calls; reset it or every
    // second row silently loses its badge.
    STAR_RE.lastIndex = 0;

    const texts = cells.map(cellText);
    const name = texts[0] ?? "";
    const price = priceIndex > 0 && priceIndex < texts.length
      ? { label: headers[priceIndex] ?? "", text: texts[priceIndex] ?? "" }
      : null;

    const rest: ResultCell[] = [];
    for (let i = 1; i < texts.length; i++) {
      if (i === priceIndex) continue;
      const text = texts[i] ?? "";
      if (!text || text === "—" || text === "-") continue;
      rest.push({ label: headers[i] ?? "", text });
    }

    rows.push({ name, recommended, price, rest });
  }

  if (!rows.length) return null;
  return { headers, rows, priceIndex };
}

/**
 * Split a run-on attribute value into readable parts.
 *
 * "Sharing Transfer: ₹1,793 · Without Transfer: Included (₹0)" becomes two
 * entries. A value with no separator comes back as a single-entry array, so
 * callers never need to special-case it.
 */
export function splitParts(text: string): string[] {
  return text
    .split(PART_SEPARATOR)
    .map((p) => p.trim())
    .filter(Boolean);
}

/**
 * Does this cell read as a number the eye should scan down a column?
 * Used to switch on tabular figures without forcing them onto prose.
 */
export function looksNumeric(text: string): boolean {
  return /\d/.test(text) && /^[^A-Za-z]*\d[\d\s.,₹$€£/+-]*[^A-Za-z]*$/.test(text);
}
