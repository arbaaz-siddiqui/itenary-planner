"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import {
  dateTimeLabel,
  formatCount,
  relativeTime,
  searchQuery,
  searchSummary,
  toolLabel,
  type AdminSearchList,
  type SearchFilters,
} from "@/lib/admin";
import { Select } from "@/components/Select";
import { Empty, ErrorNote, FOCUS, Loading, PageHeader, Pager, StatusPill } from "@/components/admin/AdminUi";

const PAGE_SIZE = 50;

const INPUT =
  "w-full rounded-[10px] border border-border bg-bg px-3 py-2.5 text-sm text-fg outline-none [color-scheme:dark] placeholder:text-fg-muted focus-visible:border-brand-blue focus-visible:ring-2 focus-visible:ring-brand-blue/20 [[data-theme=light]_&]:[color-scheme:light]";

function filtersFrom(sp: URLSearchParams): SearchFilters {
  return {
    tool: sp.get("tool") ?? "",
    user_id: sp.get("user_id") ?? "",
    q: sp.get("q") ?? "",
    errors_only: sp.get("errors_only") === "true",
    from: sp.get("from") ?? "",
    to: sp.get("to") ?? "",
    page: Math.max(1, Number(sp.get("page") ?? 1) || 1),
  };
}

function SearchesView() {
  const router = useRouter();
  const params = useSearchParams();
  const [filters, setFilters] = useState<SearchFilters>(() => filtersFrom(new URLSearchParams(params?.toString() ?? "")));
  const [text, setText] = useState(filters.q ?? "");
  const [data, setData] = useState<AdminSearchList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Every filter lives in the URL, so a filtered view can be shared or reloaded.
  const current = useRef(filters);
  const update = useCallback(
    (patch: Partial<SearchFilters>) => {
      const next = { ...current.current, ...patch, page: patch.page ?? 1 };
      current.current = next;
      setFilters(next);
      router.replace(`/admin/searches${searchQuery(next)}`, { scroll: false });
    },
    [router],
  );

  useEffect(() => {
    const t = setTimeout(() => {
      if ((filters.q ?? "") !== text) update({ q: text });
    }, 300);
    return () => clearTimeout(t);
  }, [text, filters.q, update]);

  const load = useCallback(() => {
    setError(null);
    setLoading(true);
    api
      .adminSearches({ ...filters, page: filters.page ?? 1 })
      .then((d) => setData(d))
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load searches"))
      .finally(() => setLoading(false));
  }, [filters]);

  useEffect(load, [load]);

  const toolOptions = useMemo(() => {
    const tools = new Set(data?.tools ?? []);
    if (filters.tool) tools.add(filters.tool);
    return [
      { value: "", label: "All tools" },
      ...[...tools].sort((a, b) => toolLabel(a).localeCompare(toolLabel(b))).map((t) => ({ value: t, label: toolLabel(t) })),
    ];
  }, [data?.tools, filters.tool]);

  const userEmail = filters.user_id ? data?.searches[0]?.email : null;
  const anyFilter = Boolean(filters.tool || filters.user_id || filters.q || filters.errors_only || filters.from || filters.to);

  return (
    <>
      <PageHeader
        title="Searches"
        subtitle={data ? `${formatCount(data.total)} searches match. Newest first.` : "Every search the agent ran for a customer."}
      />

      <div className="mb-4 grid gap-2.5 rounded-2xl border border-border bg-surface p-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_auto]">
        <label className="relative block">
          <span className="sr-only">Search destinations, customers and errors</span>
          <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-fg-muted" aria-hidden />
          <input
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Destination, email or error"
            className={`${INPUT} pl-9`}
          />
        </label>
        <Select<string> id="tool-filter" value={filters.tool ?? ""} options={toolOptions} onChange={(tool) => update({ tool })} />
        <label className="block">
          <span className="sr-only">From date</span>
          <input type="date" value={filters.from ?? ""} max={filters.to || undefined} onChange={(e) => update({ from: e.target.value })} className={INPUT} aria-label="From date" />
        </label>
        <label className="block">
          <span className="sr-only">To date</span>
          <input type="date" value={filters.to ?? ""} min={filters.from || undefined} onChange={(e) => update({ to: e.target.value })} className={INPUT} aria-label="To date" />
        </label>
        <button
          type="button"
          aria-pressed={Boolean(filters.errors_only)}
          onClick={() => update({ errors_only: !filters.errors_only })}
          className={`press inline-flex h-[42px] items-center justify-center gap-2 rounded-[10px] border px-3.5 text-sm whitespace-nowrap transition ${FOCUS} ${
            filters.errors_only
              ? "border-brand-red bg-brand-red/12 font-medium text-brand-red"
              : "border-border bg-bg text-fg hover:border-fg-muted/40"
          }`}
        >
          <AlertTriangle size={15} aria-hidden />
          Failed only
        </button>
      </div>

      {anyFilter && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
          {filters.user_id && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface py-1 pr-1 pl-3 text-fg">
              Customer: {userEmail ?? "selected user"}
              <button type="button" aria-label="Show every customer" onClick={() => update({ user_id: "" })} className={`grid h-5 w-5 place-items-center rounded-full hover:bg-surface-2 ${FOCUS}`}>
                <X size={12} aria-hidden />
              </button>
            </span>
          )}
          <button
            type="button"
            onClick={() => {
              setText("");
              update({ tool: "", user_id: "", q: "", errors_only: false, from: "", to: "" });
            }}
            className={`rounded-full px-2 py-1 text-link hover:underline ${FOCUS}`}
          >
            Clear filters
          </button>
        </div>
      )}

      {error ? (
        <ErrorNote message={error} onRetry={load} />
      ) : !data ? (
        <Loading label="Loading searches" />
      ) : data.searches.length === 0 ? (
        <Empty>{anyFilter ? "No searches match these filters." : "No searches yet."}</Empty>
      ) : (
        <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"} aria-busy={loading}>
          {/* Desktop: a table. */}
          <div className="hidden overflow-hidden rounded-2xl border border-border bg-surface md:block">
            <table className="w-full table-fixed text-left text-sm">
              <thead className="border-b border-border bg-surface-2/60 text-xs text-fg-muted">
                <tr>
                  <th className="w-[130px] px-4 py-3 font-medium">When</th>
                  <th className="w-[22%] px-4 py-3 font-medium">Customer</th>
                  <th className="w-[17%] px-4 py-3 font-medium">Tool</th>
                  <th className="px-4 py-3 font-medium">What they searched</th>
                  <th className="w-[120px] px-4 py-3 font-medium">Result</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.searches.map((s) => (
                  <tr key={s.id} className="align-top">
                    <td className="px-4 py-3 text-fg-muted" title={dateTimeLabel(s.created_at)}>
                      {relativeTime(s.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <Link href={`/admin/users/${s.user_id}`} className={`block truncate text-link hover:underline ${FOCUS}`}>
                        {s.email}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <p className="truncate text-fg">{toolLabel(s.tool)}</p>
                    </td>
                    <td className="px-4 py-3">
                      <p className="break-words text-fg">{searchSummary(s)}</p>
                      {s.error && <p className="mt-1 line-clamp-2 text-xs break-words text-brand-red">{s.error}</p>}
                      {s.session_id && (
                        <Link href={`/admin/chats/${s.session_id}`} className={`mt-1 inline-block text-xs text-link hover:underline ${FOCUS}`}>
                          Open chat
                        </Link>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <StatusPill error={s.error} count={s.result_count} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Phones: one card per search. */}
          <ul className="space-y-2 md:hidden">
            {data.searches.map((s) => (
              <li key={s.id} className="rounded-2xl border border-border bg-surface p-3.5">
                <div className="flex items-start justify-between gap-3">
                  <p className="min-w-0 text-sm font-medium text-fg">{toolLabel(s.tool)}</p>
                  <StatusPill error={s.error} count={s.result_count} />
                </div>
                <p className="mt-1 text-sm break-words text-fg">{searchSummary(s)}</p>
                {s.error && <p className="mt-1 line-clamp-3 text-xs break-words text-brand-red">{s.error}</p>}
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted">
                  <Link href={`/admin/users/${s.user_id}`} className={`max-w-full truncate text-link hover:underline ${FOCUS}`}>
                    {s.email}
                  </Link>
                  <span>{relativeTime(s.created_at)}</span>
                  {s.session_id && (
                    <Link href={`/admin/chats/${s.session_id}`} className={`text-link hover:underline ${FOCUS}`}>
                      Open chat
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ul>

          <Pager page={filters.page ?? 1} pageSize={data.page_size || PAGE_SIZE} total={data.total} onPage={(page) => update({ page })} />
        </div>
      )}
    </>
  );
}

export default function AdminSearchesPage() {
  return (
    <Suspense fallback={<Loading label="Loading searches" />}>
      <SearchesView />
    </Suspense>
  );
}
