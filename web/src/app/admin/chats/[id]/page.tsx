"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ChevronDown, Eye, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import {
  dateTimeLabel,
  fullNameOf,
  prettyJson,
  toolLabel,
  type AdminToolCall,
  type AdminTranscript,
} from "@/lib/admin";
import type { Message } from "@/lib/types";
import { CardActionsProvider, type CardActions } from "@/lib/cardActions";
import { MessageList } from "@/components/MessageList";
import { ErrorNote, FOCUS, Loading } from "@/components/admin/AdminUi";

const READ_ONLY: CardActions = {
  open: () => {},
  isSaved: () => false,
  toggleSave: () => {},
  book: () => {},
  readOnly: true,
};

function ToolCalls({ calls, at }: { calls: AdminToolCall[]; at: string }) {
  return (
    <div className="mt-3 space-y-2">
      <p className="text-[11px] text-fg-muted">{dateTimeLabel(at)}</p>
      {calls.length > 0 && (
        <details className="group rounded-xl border border-border bg-surface/60 [&_summary::-webkit-details-marker]:hidden">
          <summary className={`press flex cursor-pointer list-none items-center justify-between gap-3 px-3.5 py-2.5 text-sm text-fg ${FOCUS}`}>
            <span className="inline-flex items-center gap-2">
              <Wrench size={14} className="text-fg-muted" aria-hidden />
              {calls.length === 1 ? "1 tool call" : `${calls.length} tool calls`}
              <span className="hidden text-fg-muted sm:inline">
                · {[...new Set(calls.map((c) => toolLabel(c.tool_name)))].join(", ")}
              </span>
            </span>
            <ChevronDown size={15} aria-hidden className="shrink-0 text-fg-muted transition-transform group-open:rotate-180" />
          </summary>
          <ol className="space-y-3 border-t border-border px-3.5 py-3">
            {calls.map((c, i) => (
              <li key={i} className="min-w-0">
                <p className="text-sm font-medium text-fg">
                  {toolLabel(c.tool_name)} <span className="font-mono text-xs font-normal text-fg-muted">{c.tool_name}</span>
                </p>
                <p className="mt-2 text-[11px] font-medium text-fg-muted">Input</p>
                <pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-surface-2 p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-fg">
                  {prettyJson(c.input)}
                </pre>
                <p className="mt-2 text-[11px] font-medium text-fg-muted">
                  Result
                  {c.output_chars && c.output_chars > c.output_preview.length
                    ? ` (first ${c.output_preview.length.toLocaleString("en-IN")} of ${c.output_chars.toLocaleString("en-IN")} characters)`
                    : ""}
                </p>
                <pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-surface-2 p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-fg">
                  {prettyJson(c.output_preview)}
                </pre>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

export default function AdminTranscriptPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id ?? "");
  const [data, setData] = useState<AdminTranscript | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminTranscript(id)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load this chat"));
  }, [id]);

  useEffect(load, [load]);

  const { messages, extras } = useMemo(() => {
    const extras = new Map<string, { calls: AdminToolCall[]; at: string }>();
    const messages: Message[] = [];
    for (const m of data?.messages ?? []) {
      if (m.role === "system") continue;
      extras.set(m.id, { calls: m.tool_calls, at: m.created_at });
      messages.push({
        id: m.id,
        role: m.role,
        content: m.content,
        created_at: m.created_at,
        options: m.options,
        feedback: m.feedback,
      });
    }
    return { messages, extras };
  }, [data]);

  const back = (
    <Link
      href={data ? `/admin/users/${data.user.id}` : "/admin/users"}
      className={`mb-4 inline-flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg ${FOCUS}`}
    >
      <ArrowLeft size={15} aria-hidden />
      {data ? fullNameOf(data.user) : "Users"}
    </Link>
  );

  if (error) {
    return (
      <>
        {back}
        <ErrorNote message={error === "Not Found" ? "This chat does not exist or was deleted." : error} onRetry={load} />
      </>
    );
  }
  if (!data) {
    return (
      <>
        {back}
        <Loading label="Loading the chat" />
      </>
    );
  }

  return (
    <>
      {/* Same column as the bubbles below (MessageList is 820px with 16px sides). */}
      <div className="mx-auto max-w-[820px] sm:px-4">
      {back}
      <header className="mb-4">
        <h1 className="font-display text-2xl font-semibold tracking-[-0.02em] break-words text-fg">{data.session.title || "Untitled"}</h1>
        <p className="mt-1 text-sm break-words text-fg-muted">
          <Link href={`/admin/users/${data.user.id}`} className={`text-link hover:underline ${FOCUS}`}>
            {data.user.email}
          </Link>{" "}
          · started {dateTimeLabel(data.session.created_at)} · {messages.length} messages
        </p>
      </header>
      <p className="mb-2 inline-flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs text-fg-muted">
        <Eye size={14} aria-hidden />
        Read only. Opening a chat is recorded in the admin audit log.
      </p>
      </div>

      <div className="-mx-4 sm:mx-0">
        {messages.length ? (
          <CardActionsProvider value={READ_ONLY}>
            <MessageList
              messages={messages}
              readOnly
              extraFor={(m) => {
                const x = extras.get(m.id);
                return x && m.role === "assistant" ? <ToolCalls calls={x.calls} at={x.at} /> : null;
              }}
              onSuggest={() => {}}
              onRegenerate={() => {}}
              onEdit={() => {}}
              onRate={async () => {}}
            />
          </CardActionsProvider>
        ) : (
          <p className="px-4 py-10 text-center text-sm text-fg-muted">This chat has no messages.</p>
        )}
      </div>
    </>
  );
}
