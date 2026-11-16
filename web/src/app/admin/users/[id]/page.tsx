"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ChevronRight, Pin, Search } from "lucide-react";
import { api } from "@/lib/api";
import {
  dateLabel,
  dateTimeLabel,
  factRows,
  formatCount,
  fullNameOf,
  relativeTime,
  type AdminUserDetail,
} from "@/lib/admin";
import { Avatar, Empty, ErrorNote, FOCUS, KpiTile, Loading, Panel } from "@/components/admin/AdminUi";

export default function AdminUserPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id ?? "");
  const [data, setData] = useState<AdminUserDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminUser(id)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load this user"));
  }, [id]);

  useEffect(load, [load]);

  const back = (
    <Link href="/admin/users" className={`mb-4 inline-flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg ${FOCUS}`}>
      <ArrowLeft size={15} aria-hidden />
      All users
    </Link>
  );

  if (error) {
    return (
      <>
        {back}
        <ErrorNote message={error === "Not Found" ? "This user does not exist." : error} onRetry={load} />
      </>
    );
  }
  if (!data) {
    return (
      <>
        {back}
        <Loading label="Loading user" />
      </>
    );
  }

  const name = fullNameOf(data.user);
  const facts = factRows(data.facts);
  const messages = data.sessions.reduce((n, s) => n + s.messages, 0);
  const lastActive = data.last_active;

  return (
    <>
      {back}
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={name} />
          <div className="min-w-0">
            <h1 className="truncate font-display text-2xl font-semibold tracking-[-0.02em] text-fg">{name}</h1>
            <p className="truncate text-sm text-fg-muted">
              {data.user.email} · joined {dateLabel(data.user.created_at)}
            </p>
          </div>
        </div>
        <Link
          href={`/admin/searches?user_id=${data.user.id}`}
          className={`press inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-surface px-3 text-sm text-fg hover:border-brand-blue ${FOCUS}`}
        >
          <Search size={15} aria-hidden />
          See searches
        </Link>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiTile label="Chats" value={formatCount(data.sessions.length)} note={`${formatCount(messages)} messages`} />
        <KpiTile label="Searches" value={formatCount(data.searches)} note={data.last_search ? `Last ${relativeTime(data.last_search)}` : "None yet"} />
        <KpiTile label="Saved cards" value={formatCount(data.saved_cards)} note="Hearted results" />
        <KpiTile label="Last active" value={relativeTime(lastActive)} note={lastActive ? dateTimeLabel(lastActive) : "No chats yet"} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Panel title="Preferences and remembered facts">
          {facts.length ? (
            <dl className="divide-y divide-border text-sm">
              {facts.map((f) => (
                <div key={f.label} className="flex items-start justify-between gap-4 py-2.5 first:pt-0 last:pb-0">
                  <dt className="text-fg-muted">{f.label}</dt>
                  <dd className="min-w-0 text-right break-words text-fg">{f.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <Empty>Nothing set or remembered yet.</Empty>
          )}
        </Panel>

        <Panel title={`Chats (${formatCount(data.sessions.length)})`}>
          {data.sessions.length ? (
            <ul className="-mx-2 divide-y divide-border">
              {data.sessions.map((s) => (
                <li key={s.id}>
                  <Link
                    href={`/admin/chats/${s.id}`}
                    className={`flex items-center gap-3 rounded-lg px-2 py-3 hover:bg-surface-2/60 ${FOCUS}`}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-1.5 text-sm font-medium text-fg">
                        {s.pinned && <Pin size={13} className="shrink-0 text-fg-muted" aria-label="Pinned" />}
                        <span className="truncate">{s.title || "Untitled"}</span>
                      </p>
                      <p className="mt-0.5 text-xs text-fg-muted tabular-nums">
                        Started {dateLabel(s.created_at)} · last message {relativeTime(s.updated_at)} ·{" "}
                        {formatCount(s.messages)} messages · {formatCount(s.searches)} searches
                      </p>
                    </div>
                    <ChevronRight size={16} className="shrink-0 text-fg-muted" aria-hidden />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No chats yet.</Empty>
          )}
        </Panel>
      </div>
    </>
  );
}
