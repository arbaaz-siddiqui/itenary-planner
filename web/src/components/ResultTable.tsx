"use client";

import { memo } from "react";
import { looksNumeric, splitParts, type ResultCell, type ResultRow, type ResultTable as Parsed } from "@/lib/resultTable";
import { Carousel } from "./Carousel";
import { CardCarousel } from "./cards/CardCarousel";
import { toCardRefs } from "@/lib/cardRefs";
import { oneCardPerOption } from "@/lib/optionMatch";
import type { OptionItem } from "@/lib/types";

/**
 * Row name -> the option row behind it.
 *
 * The agent's table is plain text: no picture, no supplier ids. The option
 * rows streamed with the message carry both, so matching by name lets ONE
 * card carry the tour, its image and its "Show details" link.
 */
export type OptionLookup = (name: string, hint?: { price?: string | null; nth?: number }) => OptionItem | null;

/**
 * A result comparison (tours, hotels, flights) the agent wrote as a markdown
 * table.
 *
 * Two renderings of the SAME data, switched by CSS rather than by measuring
 * the viewport in JS — a `useMediaQuery` here would render the wrong branch on
 * the server and flash on hydration, and this sits on the streaming path where
 * every token re-renders.
 *
 *   cards  — always on narrow, and at ANY width once the table is wide enough
 *            (>= 5 columns) that a row could not be read across.
 *   table  — wide viewports only, for scanning many rows at once.
 */

/** A table this wide can never show name and price together on one screen. */
const WIDE_TABLE_COLUMNS = 5;

function RecommendedBadge() {
  return (
    <span className="inline-flex shrink-0 items-center rounded-full border border-accent/40 px-1.5 py-px text-[10px] font-medium tracking-wide text-accent uppercase">
      Recommended
    </span>
  );
}

/** The price, rendered as the loudest thing in the card after the name. */
function PriceBlock({ cell }: { cell: ResultCell | null }) {
  if (!cell?.text) return null;
  return (
    <div className="shrink-0 text-right">
      <div className="text-[15px] leading-snug font-semibold tabular-nums">{cell.text}</div>
      {cell.label && <div className="text-[11px] leading-tight text-fg-muted">{cell.label}</div>}
    </div>
  );
}

/**
 * One secondary attribute. Values carrying "·" (the transfer column, mostly)
 * break into separate lines instead of one unreadable run-on string.
 */
function Attribute({ cell }: { cell: ResultCell }) {
  const parts = splitParts(cell.text);

  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
      <dt className="shrink-0 text-[11px] leading-5 text-fg-muted sm:w-28">{cell.label}</dt>
      <dd className="min-w-0 text-[13px] leading-5">
        {parts.length > 1 ? (
          <ul className="flex flex-col gap-0.5">
            {parts.map((p, i) => (
              <li key={i} className={looksNumeric(p) ? "tabular-nums" : undefined}>
                {p}
              </li>
            ))}
          </ul>
        ) : (
          <span className={looksNumeric(cell.text) ? "tabular-nums" : undefined}>{cell.text}</span>
        )}
      </dd>
    </div>
  );
}

/** A table row with no supplier data behind it: the agent's own text only. */
function Card({ row }: { row: ResultRow }) {
  return (
    <li className="h-full rounded-2xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {row.recommended && (
            <div className="mb-1">
              <RecommendedBadge />
            </div>
          )}
          <p className="text-sm leading-snug font-semibold break-words">{row.name}</p>
        </div>
        <PriceBlock cell={row.price} />
      </div>

      {row.rest.length > 0 && (
        <dl className="mt-2.5 flex flex-col gap-1.5 border-t border-border pt-2.5">
          {row.rest.map((c, i) => (
            <Attribute key={i} cell={c} />
          ))}
        </dl>
      )}
    </li>
  );
}

function Cards({ table }: { table: Parsed }) {
  // A carousel, not a stack: a 17-tour search became a wall the customer had
  // to scroll past to reach the rest of the reply.
  return (
    <Carousel count={table.rows.length}>
      {table.rows.map((row, i) => (
        <ul
          key={i}
          className="ui-list w-[85%] shrink-0 snap-start sm:w-[calc(50%-0.375rem)]"
        >
          <Card row={row} />
        </ul>
      ))}
    </Carousel>
  );
}

/**
 * The wide-viewport table. Unlike the generic `.md-table-wrap` rules this
 * fits the bubble (`table-fixed`, no `width: max-content`): the name column
 * wraps, and only the price column is held on one line.
 */
function WideTable({ table }: { table: Parsed }) {
  return (
    <div className="md-result-table overflow-x-auto rounded-lg border border-border">
      <table>
        <thead>
          <tr>
            {table.headers.map((h, i) => (
              <th key={i} scope="col" data-price={i === table.priceIndex || undefined}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, r) => (
            <tr key={r}>
              {table.headers.map((_, c) => {
                if (c === 0) {
                  return (
                    <th key={c} scope="row" data-name="">
                      {row.recommended && (
                        <span className="mr-1.5 inline-block align-middle">
                          <RecommendedBadge />
                        </span>
                      )}
                      {row.name}
                    </th>
                  );
                }
                if (c === table.priceIndex) {
                  return (
                    <td key={c} data-price="">
                      {row.price?.text ?? ""}
                    </td>
                  );
                }
                // `rest` dropped empty cells and skipped name+price, so map
                // back by label rather than by position.
                const cell = row.rest.find((x) => x.label === table.headers[c]);
                if (!cell) return <td key={c} />;
                const parts = splitParts(cell.text);
                return (
                  <td key={c}>
                    {parts.length > 1 ? (
                      <ul className="flex flex-col gap-0.5">
                        {parts.map((p, i) => (
                          <li key={i} className={looksNumeric(p) ? "tabular-nums" : undefined}>
                            {p}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      cell.text
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const ResultComparison = memo(function ResultComparison({
  table,
  optionFor,
}: {
  table: Parsed;
  optionFor?: OptionLookup;
}) {
  // Rows the agent wrote about real results become the real cards: photo,
  // supplier fields, details and booking. Only rows with nothing behind them
  // stay as text cards.
  // Two results can share a name (two Toyota Hiace transfers at different
  // prices). The row's own price picks the right one; failing that, its
  // position among rows of that name.
  const seen = new Map<string, number>();
  const matched = oneCardPerOption(
    table.rows.map((row) => {
      const nth = seen.get(row.name) ?? 0;
      seen.set(row.name, nth + 1);
      return { row, item: optionFor?.(row.name, { price: row.price?.text ?? null, nth }) ?? null };
    }),
  );
  const cards = toCardRefs(matched.flatMap((m) => (m.item ? [m.item] : [])));
  const rest = matched.filter((m) => !m.item).map((m) => m.row);

  if (cards.length) {
    return (
      <div className="md-result mt-3.5">
        <CardCarousel cards={cards} layout={cards[0].kind === "flight" ? "list" : "carousel"} />
        {rest.length > 0 && <Cards table={{ ...table, rows: rest }} />}
      </div>
    );
  }

  // Too many columns to read across at any width: cards only, everywhere.
  if (table.headers.length >= WIDE_TABLE_COLUMNS) {
    return (
      <div className="md-result mt-3.5">
        <Cards table={table} />
      </div>
    );
  }

  // Narrow enough to scan: cards on phones, table from `sm` up. Both branches
  // are in the DOM; CSS picks one, so there is no hydration mismatch.
  return (
    <div className="md-result mt-3.5">
      <div className="sm:hidden">
        <Cards table={table} />
      </div>
      <div className="hidden sm:block">
        <WideTable table={table} />
      </div>
    </div>
  );
});
