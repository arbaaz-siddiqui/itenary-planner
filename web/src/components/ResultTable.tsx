"use client";

import { memo } from "react";
import { looksNumeric, splitParts, type ResultCell, type ResultRow, type ResultTable as Parsed } from "@/lib/resultTable";
import { SafeImage } from "./SafeImage";
import { TourDetails } from "./TourDetails";
import { Carousel } from "./Carousel";
import type { OptionItem } from "@/lib/types";

/**
 * Row name -> the option row behind it.
 *
 * The agent's table is plain text: no picture, no supplier ids. The option
 * rows streamed with the message carry both, so matching by name lets ONE
 * card carry the tour, its image and its "Show details" link.
 */
export type OptionLookup = (name: string) => OptionItem | null;

function pickImage(o: OptionItem | null): unknown {
  if (!o) return null;
  if (typeof o.image_url === "string" && o.image_url) return o.image_url;
  const many = o.image_urls;
  if (Array.isArray(many) && typeof many[0] === "string") return many[0];
  return null;
}

function pickNumber(o: OptionItem | null, k: string): number | null {
  const v = o?.[k];
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() && Number.isFinite(Number(v))) return Number(v);
  return null;
}

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

function Card({ row, optionFor }: { row: ResultRow; optionFor?: OptionLookup }) {
  const option = optionFor?.(row.name) ?? null;
  const image = pickImage(option);
  // Only when the supplier ids came through: a details link that cannot fetch
  // is worse than no link.
  const tourId = pickNumber(option, "tour_id");
  const optionId = pickNumber(option, "option_id");
  const supplierId = pickNumber(option, "supplier_id");

  return (
    <li className="h-full overflow-hidden rounded-lg border border-border bg-surface">
      {image != null && (
        <SafeImage src={image} alt={row.name} ratio="aspect-[16/9]" className="rounded-none" />
      )}
      <div className="p-3">
      {/* Name and price on one line, never separated by a scroll. `min-w-0`
          on the name is what lets a 90-character tour title wrap instead of
          pushing the price off the card. */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {row.recommended && (
            <div className="mb-1">
              <RecommendedBadge />
            </div>
          )}
          <p className="text-[13px] leading-snug font-semibold break-words">{row.name}</p>
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

      {tourId != null && optionId != null && supplierId != null && (
        <div className="mt-2.5 border-t border-border pt-2.5">
          <TourDetails
            tourId={tourId}
            optionId={optionId}
            supplierId={supplierId}
            name={row.name}
          />
        </div>
      )}
      </div>
    </li>
  );
}

function Cards({ table, optionFor }: { table: Parsed; optionFor?: OptionLookup }) {
  // A carousel, not a stack: a 17-tour search became a wall the customer had
  // to scroll past to reach the rest of the reply.
  return (
    <Carousel count={table.rows.length}>
      {table.rows.map((row, i) => (
        <ul
          key={i}
          className="w-[85%] shrink-0 snap-start sm:w-[calc(50%-0.375rem)]"
        >
          <Card row={row} optionFor={optionFor} />
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
  // Too many columns to read across at any width — cards only, everywhere.
  if (table.headers.length >= WIDE_TABLE_COLUMNS) {
    return (
      <div className="md-result mt-3.5">
        <Cards table={table} optionFor={optionFor} />
      </div>
    );
  }

  // Narrow enough to scan: cards on phones, table from `sm` up. Both branches
  // are in the DOM; CSS picks one, so there is no hydration mismatch.
  return (
    <div className="md-result mt-3.5">
      <div className="sm:hidden">
        <Cards table={table} optionFor={optionFor} />
      </div>
      <div className="hidden sm:block">
        <WideTable table={table} />
      </div>
    </div>
  );
});
