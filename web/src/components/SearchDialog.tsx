"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MessageSquare, Search } from "lucide-react";
import type { ChatSession } from "@/lib/types";

/** Command-palette search over chat titles. Ctrl/Cmd+K opens it. */
export function SearchDialog({
  open,
  sessions,
  onPick,
  onClose,
}: {
  open: boolean;
  sessions: ChatSession[];
  onPick: (id: string) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const hits = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = needle ? sessions.filter((s) => (s.title || "").toLowerCase().includes(needle)) : sessions;
    return list.slice(0, 20);
  }, [q, sessions]);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setCursor(0);
    requestAnimationFrame(() => input.current?.focus());
  }, [open]);

  useEffect(() => setCursor(0), [q]);

  if (!open) return null;

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") onClose();
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, hits.length - 1));
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    }
    if (e.key === "Enter" && hits[cursor]) onPick(hits[cursor].id);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[12vh]" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search chats"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKey}
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"
      >
        <div className="flex items-center gap-2 border-b border-border px-4">
          <Search size={17} className="text-fg-muted" aria-hidden />
          <input
            ref={input}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search chats…"
            aria-label="Search chats"
            className="h-12 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-muted"
          />
          <kbd className="rounded border border-border px-1.5 text-[10px] text-fg-muted">Esc</kbd>
        </div>
        <ul className="max-h-[50vh] overflow-y-auto p-2" role="listbox" aria-label="Chats">
          {hits.length === 0 ? (
            <li className="px-3 py-6 text-center text-sm text-fg-muted">No chats match “{q}”.</li>
          ) : (
            hits.map((s, i) => (
              <li key={s.id} role="option" aria-selected={i === cursor}>
                <button
                  type="button"
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => onPick(s.id)}
                  className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm ${
                    i === cursor ? "bg-brand-blue/15 text-fg" : "text-fg"
                  }`}
                >
                  <MessageSquare size={15} className="shrink-0 text-fg-muted" aria-hidden />
                  <span className="truncate">{s.title || "Untitled"}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
