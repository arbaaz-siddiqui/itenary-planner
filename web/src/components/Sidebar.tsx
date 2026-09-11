"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatSession, User } from "@/lib/types";

function SessionRow({
  session,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  session: ChatSession;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.title);
  const [confirming, setConfirming] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  function commit() {
    const t = draft.trim();
    setEditing(false);
    if (t && t !== session.title) onRename(t);
    else setDraft(session.title);
  }

  if (editing) {
    return (
      <li>
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setDraft(session.title);
              setEditing(false);
            }
          }}
          aria-label="Chat title"
          className="w-full rounded-lg border border-accent bg-bg px-2.5 py-2 text-sm outline-none"
        />
      </li>
    );
  }

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onSelect}
        className={`flex w-full items-center rounded-lg px-2.5 py-2 pr-14 text-left text-sm transition ${
          active ? "bg-surface-2 font-medium" : "hover:bg-surface-2"
        }`}
      >
        <span className="truncate">{session.title || "Untitled"}</span>
      </button>

      <div
        className={`absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5 ${
          confirming ? "" : "opacity-0 transition group-hover:opacity-100 focus-within:opacity-100"
        }`}
      >
        {confirming ? (
          <>
            <button
              type="button"
              onClick={onDelete}
              aria-label="Confirm delete"
              className="rounded px-1.5 py-0.5 text-[11px] font-medium text-red-500 hover:bg-red-500/10"
            >
              Delete
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              aria-label="Cancel delete"
              className="rounded px-1 py-0.5 text-[11px] text-fg-muted hover:bg-surface"
            >
              ✕
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => {
                setDraft(session.title);
                setEditing(true);
              }}
              aria-label={`Rename ${session.title}`}
              title="Rename"
              className="grid h-6 w-6 place-items-center rounded text-fg-muted hover:bg-surface hover:text-fg"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => setConfirming(true)}
              aria-label={`Delete ${session.title}`}
              title="Delete"
              className="grid h-6 w-6 place-items-center rounded text-fg-muted hover:bg-surface hover:text-red-500"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
              </svg>
            </button>
          </>
        )}
      </div>
    </li>
  );
}

export function Sidebar({
  sessions,
  activeId,
  user,
  loading,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onLogout,
}: {
  sessions: ChatSession[];
  activeId: string | null;
  user: User | null;
  loading: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onLogout: () => void;
}) {
  return (
    <div className="flex h-full flex-col bg-surface">
      <div className="p-2">
        <button
          type="button"
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-lg border border-border px-2.5 py-2 text-sm font-medium transition hover:bg-surface-2"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
          New chat
        </button>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label="Chat sessions">
        {loading ? (
          <p className="px-2.5 py-2 text-xs text-fg-muted">Loading chats…</p>
        ) : sessions.length === 0 ? (
          <p className="px-2.5 py-2 text-xs text-fg-muted">No chats yet.</p>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                active={s.id === activeId}
                onSelect={() => onSelect(s.id)}
                onRename={(t) => onRename(s.id, t)}
                onDelete={() => onDelete(s.id)}
              />
            ))}
          </ul>
        )}
      </nav>

      <div className="border-t border-border p-2">
        <div className="flex items-center gap-2 px-1 py-1">
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs text-fg-muted">{user?.email ?? "—"}</p>
          </div>
          <button
            type="button"
            onClick={onLogout}
            className="shrink-0 rounded px-2 py-1 text-xs text-fg-muted transition hover:bg-surface-2 hover:text-fg"
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
