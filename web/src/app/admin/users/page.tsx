"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronRight, Search } from "lucide-react";
import { api } from "@/lib/api";
import { dateLabel, formatCount, fullNameOf, relativeTime, type AdminUserList } from "@/lib/admin";
import { Avatar, Empty, ErrorNote, FOCUS, Loading, PageHeader, Pager } from "@/components/admin/AdminUi";

const PAGE_SIZE = 25;

export default function AdminUsersPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AdminUserList | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Search as the admin types, without a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setQuery(q);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(() => {
    setError(null);
    api
      .adminUsers(query, page, PAGE_SIZE)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load users"));
  }, [query, page]);

  useEffect(load, [load]);

  return (
    <>
      <PageHeader
        title="Users"
        subtitle={data ? `${formatCount(data.total)} accounts, most recently active first.` : "Every account, most recently active first."}
      />

      <label className="relative mb-4 block max-w-md">
        <span className="sr-only">Search users</span>
        <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-fg-muted" aria-hidden />
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by name or email"
          className="w-full rounded-[10px] border border-border bg-bg py-2.5 pr-3 pl-9 text-sm text-fg outline-none placeholder:text-fg-muted focus-visible:border-brand-blue focus-visible:ring-2 focus-visible:ring-brand-blue/20"
        />
      </label>

      {error ? (
        <ErrorNote message={error} onRetry={load} />
      ) : !data ? (
        <Loading label="Loading users" />
      ) : data.users.length === 0 ? (
        <Empty>{query ? "No users match that search." : "No accounts yet."}</Empty>
      ) : (
        <>
          {/* Desktop: a table. */}
          <div className="hidden overflow-hidden rounded-2xl border border-border bg-surface md:block">
            <table className="w-full table-fixed text-left text-sm">
              <thead className="border-b border-border bg-surface-2/60 text-xs text-fg-muted">
                <tr>
                  <th className="w-[34%] px-4 py-3 font-medium">User</th>
                  <th className="w-[14%] px-4 py-3 font-medium">Joined</th>
                  <th className="w-[16%] px-4 py-3 font-medium">Last active</th>
                  <th className="px-4 py-3 text-right font-medium">Chats</th>
                  <th className="px-4 py-3 text-right font-medium">Messages</th>
                  <th className="px-4 py-3 text-right font-medium">Searches</th>
                  <th className="w-8 px-2 py-3" aria-hidden />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.users.map((u) => (
                  <tr
                    key={u.id}
                    onClick={() => router.push(`/admin/users/${u.id}`)}
                    className="cursor-pointer transition-colors hover:bg-surface-2/60"
                  >
                    <td className="px-4 py-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <Avatar name={fullNameOf(u)} />
                        <div className="min-w-0">
                          <Link
                            href={`/admin/users/${u.id}`}
                            onClick={(e) => e.stopPropagation()}
                            className={`block truncate font-medium text-fg hover:underline ${FOCUS}`}
                          >
                            {fullNameOf(u)}
                          </Link>
                          <p className="truncate text-xs text-fg-muted">{u.email}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-fg-muted">{dateLabel(u.created_at)}</td>
                    <td className="px-4 py-3 whitespace-nowrap">{relativeTime(u.last_active)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatCount(u.chats)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatCount(u.messages)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatCount(u.searches)}</td>
                    <td className="px-2 py-3 text-fg-muted">
                      <ChevronRight size={16} aria-hidden />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Phones: one card per user. */}
          <ul className="space-y-2 md:hidden">
            {data.users.map((u) => (
              <li key={u.id}>
                <Link
                  href={`/admin/users/${u.id}`}
                  className={`press flex items-center gap-3 rounded-2xl border border-border bg-surface p-3.5 ${FOCUS}`}
                >
                  <Avatar name={fullNameOf(u)} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-fg">{fullNameOf(u)}</p>
                    <p className="truncate text-xs text-fg-muted">{u.email}</p>
                    <p className="mt-1.5 text-xs text-fg-muted tabular-nums">
                      {relativeTime(u.last_active)} · {formatCount(u.chats)} chats · {formatCount(u.searches)} searches
                    </p>
                  </div>
                  <ChevronRight size={16} className="shrink-0 text-fg-muted" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>

          <Pager page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}
