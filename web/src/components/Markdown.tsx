"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { parseResultTable } from "@/lib/resultTable";
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
              <div className="md-table-wrap" role="region" aria-label="Table" tabIndex={0}>
                <table>{children}</table>
              </div>
            );
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
