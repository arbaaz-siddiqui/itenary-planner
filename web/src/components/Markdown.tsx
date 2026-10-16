"use client";

import { Children, cloneElement, createContext, isValidElement, memo, useContext } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { parseResultTable } from "@/lib/resultTable";
import { plainSymbols } from "@/lib/plainSymbols";
import { holdPendingTable } from "@/lib/pendingTable";
import { ResultComparison, type OptionLookup } from "./ResultTable";
import { CardsLoading } from "./cards/CardCarousel";

/**
 * Assistant markdown.
 *
 * The one non-default piece is `table`. The agent answers with two very
 * different kinds of table:
 *
 *   - RESULT COMPARISONS — "Tour | Price/adult | Type | Duration | Transfer |
 *     Cancellation | Start times". Seven columns of supplier data. As a real
 *     table these are far wider than the bubble, so the customer has to scroll
 *     sideways to connect a tour name to its price. Those two facts are the
 *     whole point of the table and were never on screen together.
 *   - incidental prose tables — a two-column glossary, a day-by-day plan.
 *     These are fine as tables and must keep rendering as tables.
 *
 * `parseResultTable` tells them apart by looking for a price-ish header, and
 * returns null for everything else, which falls through to the original
 * scroll-wrapped renderer below.
 */
export const Markdown = memo(function Markdown({
  children,
  optionFor,
  streaming = false,
}: {
  children: string;
  /** While the reply streams, a result table is a half-written list. It
   *  shows as a loading carousel and becomes cards when the reply is done. */
  streaming?: boolean;
  /**
   * Supplies the option row behind a table row, by name: its picture and the
   * supplier ids the "Show details" panel needs.
   */
  optionFor?: OptionLookup;
}) {
  // A table still being written shows as the loader, not as raw "| a | b |".
  const { ready, pending } = streaming ? holdPendingTable(children) : { ready: children, pending: false };
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ node, children }) => {
            let parsed = parseResultTable(node);
            if (!parsed && optionFor) {
              // A list of results with no price column (packages: name, type,
              // nights) is still the results: render it as their cards when
              // most rows name one, instead of a wide table repeating them.
              const loose = parseResultTable(node, { requirePrice: false });
              const hits = loose ? loose.rows.filter((r) => optionFor(r.name)).length : 0;
              if (loose && hits * 2 >= loose.rows.length) parsed = loose;
            }
            if (parsed) {
              return streaming ? (
                <CardsLoading label="Loading results" />
              ) : (
                <ResultComparison table={parsed} optionFor={optionFor} />
              );
            }

            return (
              <HeadersContext.Provider value={headerTexts(node)}>
                <div className="md-table-wrap" role="region" aria-label="Table" tabIndex={0}>
                  <table>{children}</table>
                </div>
              </HeadersContext.Provider>
            );
          },
          tr: ({ children }) => <LabelledRow>{children}</LabelledRow>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {plainSymbols(ready)}
      </ReactMarkdown>
      {pending && <CardsLoading label="Loading results" />}
    </div>
  );
});

// --- Prose tables -----------------------------------------------------------
// Each body cell learns its column's header (shown as its label when a phone
// stacks the row into a card) and what it holds: a price or a number is
// right-aligned and set in tabular figures, a price also in the strong ink.

const HeadersContext = createContext<string[]>([]);

type HastNode = { type?: string; tagName?: string; value?: string; children?: HastNode[] };

function hastText(n: HastNode | undefined): string {
  if (!n) return "";
  if (n.type === "text") return n.value ?? "";
  return (n.children ?? []).map(hastText).join("");
}

function headerTexts(node: unknown): string[] {
  const thead = (node as HastNode | undefined)?.children?.find((c) => c.tagName === "thead");
  const row = thead?.children?.find((c) => c.tagName === "tr");
  return (row?.children ?? []).filter((c) => c.tagName === "th").map((c) => hastText(c).trim());
}

function reactText(n: React.ReactNode): string {
  if (n == null || typeof n === "boolean") return "";
  if (typeof n === "string" || typeof n === "number") return String(n);
  if (Array.isArray(n)) return n.map(reactText).join("");
  if (isValidElement<{ children?: React.ReactNode }>(n)) return reactText(n.props.children);
  return "";
}

const PRICE = /(₹|INR|AED)\s?[\d,.]+|\$\d[\d,.]*|[\d,.]+\s?(₹|INR|AED)/i;
const NUMBER = /^[\s~≈+-]*[\d,.]+\s*(%|h|hrs?|min|mins|km|nights?|days?|pax|adults?)?\s*$/i;

function LabelledRow({ children }: { children?: React.ReactNode }) {
  const headers = useContext(HeadersContext);
  let col = 0;
  const cells = Children.map(children, (child) => {
    if (!isValidElement<Record<string, unknown>>(child)) return child;
    const i = col++;
    const text = reactText(child.props.children as React.ReactNode).trim();
    const kind = PRICE.test(text) ? "price" : NUMBER.test(text) ? "num" : undefined;
    return cloneElement(child, { "data-label": headers[i] ?? "", "data-kind": kind });
  });
  return <tr>{cells}</tr>;
}
