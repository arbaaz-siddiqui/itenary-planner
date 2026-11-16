"use client";

// Building blocks of the admin screens: panels, KPI tiles, pure CSS/SVG
// charts and the pager. No chart library: the shapes are simple and the
// numbers come from lib/admin.ts, which is unit tested.

import { createContext, useContext } from "react";
import { ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";
import type { User } from "@/lib/types";
import { axisTicks, barPercents, formatCount, pageCount, rangeLabel, shortDay, sparkPath } from "@/lib/admin";
import { TravelLoader } from "@/components/TravelLoader";

export const AdminUserContext = createContext<User | null>(null);
export const useAdminUser = () => useContext(AdminUserContext);

export const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue";

export function PageHeader({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="font-display text-2xl font-semibold tracking-[-0.02em] text-fg sm:text-[28px]">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-fg-muted">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </header>
  );
}

export function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`min-w-0 rounded-2xl border border-border bg-surface p-4 sm:p-5 ${className}`}>
      {(title || action) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="font-display text-base font-semibold tracking-[-0.01em] text-fg">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="grid min-h-[50vh] place-items-center" aria-busy>
      <TravelLoader size="lg" label={label} />
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-center gap-3 rounded-xl border border-brand-red/30 bg-brand-red/10 px-4 py-3 text-sm text-fg">
      <span className="min-w-0 flex-1">{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className={`press inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-brand-red px-3 py-1.5 text-xs font-medium text-white ${FOCUS}`}
        >
          <RotateCcw size={13} aria-hidden />
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-fg-muted">{children}</p>;
}

export function Sparkline({ values, className = "text-brand-blue" }: { values: number[]; className?: string }) {
  const w = 120;
  const h = 32;
  const d = sparkPath(values, w, h);
  if (!d) return null;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={`h-8 w-full ${className}`} aria-hidden>
      <path d={`${d} L${w - 2} ${h} L2 ${h} Z`} fill="currentColor" opacity={0.12} />
      <path d={d} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function KpiTile({
  label,
  value,
  note,
  tone = "default",
  spark,
  sparkClass,
  icon: Icon,
}: {
  label: string;
  value: string;
  note?: React.ReactNode;
  tone?: "default" | "bad" | "good";
  spark?: number[];
  sparkClass?: string;
  icon?: React.ComponentType<{ size?: number; className?: string; "aria-hidden"?: boolean }>;
}) {
  const valueTone = tone === "bad" ? "text-brand-red" : tone === "good" ? "text-success" : "text-fg";
  return (
    <div className="flex min-w-0 flex-col rounded-2xl border border-border bg-surface p-4">
      <div className="flex items-center gap-2 text-xs font-medium text-fg-muted">
        {Icon && <Icon size={14} className="shrink-0" aria-hidden />}
        <span className="truncate">{label}</span>
      </div>
      <p className={`mt-2 truncate font-display text-[22px] leading-none font-semibold tracking-[-0.02em] tabular-nums sm:text-[26px] ${valueTone}`}>{value}</p>
      {note && <p className="mt-1.5 truncate text-xs text-fg-muted">{note}</p>}
      {spark && spark.length > 1 && (
        <div className="mt-3">
          <Sparkline values={spark} className={sparkClass} />
        </div>
      )}
    </div>
  );
}

/**
 * Columns per day. `secondary` is drawn inside each column from the bottom
 * (failed searches inside all searches).
 */
export function ColumnChart({
  days,
  values,
  secondary,
  label,
  secondaryLabel,
  colorClass = "bg-brand-blue",
  secondaryClass = "bg-brand-red",
}: {
  days: string[];
  values: number[];
  secondary?: number[];
  label: string;
  secondaryLabel?: string;
  colorClass?: string;
  secondaryClass?: string;
}) {
  const pct = barPercents(values);
  const ticks = new Set(axisTicks(days.length, 6));
  const max = Math.max(0, ...values);
  const total = values.reduce((a, b) => a + b, 0);
  return (
    <figure className="min-w-0">
      <div className="mb-2 flex items-baseline justify-between gap-2 text-xs text-fg-muted">
        <span>
          Peak {formatCount(max)} a day, {formatCount(total)} in all
        </span>
        {secondary && secondaryLabel && (
          <span className="inline-flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-sm ${secondaryClass}`} aria-hidden />
            {secondaryLabel}
          </span>
        )}
      </div>
      <div
        className="relative flex h-40 items-end gap-[2px] border-b border-border sm:gap-1"
        role="img"
        aria-label={`${label}: ${days.map((d, i) => `${shortDay(d)} ${values[i]}`).join(", ")}`}
      >
        {/* Faint guide lines at a half and the top. */}
        <span className="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-border/70" aria-hidden />
        <span className="pointer-events-none absolute inset-x-0 top-1/2 border-t border-dashed border-border/70" aria-hidden />
        {values.map((v, i) => {
          const sub = secondary?.[i] ?? 0;
          const subPct = v > 0 ? Math.min(100, (sub / v) * 100) : 0;
          return (
            <div
              key={days[i] ?? i}
              title={`${shortDay(days[i] ?? "")}: ${formatCount(v)}${secondary && secondaryLabel ? `, ${formatCount(sub)} ${secondaryLabel.toLowerCase()}` : ""}`}
              className="group relative flex h-full min-w-0 flex-1 items-end"
            >
              <div
                className={`admin-bar relative w-full overflow-hidden rounded-t-[3px] ${colorClass} opacity-85 transition-opacity group-hover:opacity-100`}
                style={{ height: `${v > 0 ? Math.max(pct[i], 2) : 0}%` }}
              >
                {subPct > 0 && (
                  <div className={`absolute inset-x-0 bottom-0 ${secondaryClass}`} style={{ height: `${subPct}%` }} />
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex gap-[2px] text-[10px] text-fg-muted sm:gap-1" aria-hidden>
        {days.map((d, i) => (
          <span key={d} className="relative min-w-0 flex-1">
            {ticks.has(i) && (
              <span
                className={`absolute top-0 whitespace-nowrap ${
                  i === 0 ? "left-0" : i === days.length - 1 ? "right-0" : "left-1/2 -translate-x-1/2"
                }`}
              >
                {shortDay(d)}
              </span>
            )}
          </span>
        ))}
      </div>
      <div className="h-3" aria-hidden />
    </figure>
  );
}

/** Ranked horizontal bars (services, destinations). */
export function BarList({
  rows,
  colorClass = "bg-brand-blue",
}: {
  rows: { label: string; value: number; failed?: number; hint?: string }[];
  colorClass?: string;
}) {
  const pct = barPercents(rows.map((r) => r.value));
  return (
    <ul className="space-y-3">
      {rows.map((r, i) => (
        <li key={r.label} className="min-w-0">
          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate text-fg">{r.label}</span>
            <span className="shrink-0 text-xs text-fg-muted tabular-nums">
              {formatCount(r.value)}
              {r.failed ? <span className="text-brand-red"> · {formatCount(r.failed)} failed</span> : null}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-surface-2" title={r.hint}>
            <div className={`admin-bar-x relative h-full rounded-full ${colorClass}`} style={{ width: `${Math.max(pct[i], r.value > 0 ? 2 : 0)}%` }}>
              {r.failed ? (
                <div className="absolute inset-y-0 right-0 bg-brand-red" style={{ width: `${Math.min(100, (r.failed / Math.max(1, r.value)) * 100)}%` }} />
              ) : null}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = pageCount(total, pageSize);
  const btn = `press inline-flex h-9 items-center gap-1 rounded-lg border border-border bg-surface px-3 text-sm text-fg hover:border-brand-blue disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-border ${FOCUS}`;
  return (
    <nav aria-label="Pages" className="mt-4 flex items-center justify-between gap-3">
      <p className="text-xs text-fg-muted tabular-nums">{rangeLabel(page, pageSize, total)}</p>
      <div className="flex items-center gap-2">
        <button type="button" className={btn} disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft size={15} aria-hidden />
          Previous
        </button>
        <span className="text-xs text-fg-muted tabular-nums">
          {page} / {pages}
        </span>
        <button type="button" className={btn} disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
          <ChevronRight size={15} aria-hidden />
        </button>
      </div>
    </nav>
  );
}

export function Avatar({ name }: { name: string }) {
  return (
    <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-blue/15 text-sm font-semibold text-link">
      {(name.trim()[0] ?? "?").toUpperCase()}
    </span>
  );
}

export function StatusPill({ error, count }: { error: string | null; count?: number | null }) {
  if (error) {
    return (
      <span className="inline-flex items-center rounded-full bg-brand-red/12 px-2 py-0.5 text-xs font-medium text-brand-red">Failed</span>
    );
  }
  if (count === 0) {
    return (
      <span className="inline-flex items-center rounded-full bg-rating/15 px-2 py-0.5 text-xs font-medium text-fg">No results</span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-success/12 px-2 py-0.5 text-xs font-medium text-success tabular-nums">
      {count == null ? "Done" : `${formatCount(count)} results`}
    </span>
  );
}
