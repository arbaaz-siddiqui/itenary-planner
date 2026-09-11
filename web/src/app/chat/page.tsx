"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, isUnauthorized, MOCK_MODE } from "@/lib/api";
import type { ChatSession, Message, OptionGroup, ToolCall, User } from "@/lib/types";
import { useStreamSegments } from "@/lib/useStreamSegments";
import { Sidebar } from "@/components/Sidebar";
import { MessageList } from "@/components/MessageList";
import { Composer } from "@/components/Composer";

const ACTIVE_KEY = "active-session-id";

export default function ChatPage() {
  const router = useRouter();

  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  /** Which bubble the live segments belong to. */
  const [liveId, setLiveId] = useState<string | null>(null);

  // Live segments + the idle "Thinking…" timer. Segments are a streaming-only
  // view; the settled message still persists as content + tool_calls.
  const stream = useStreamSegments();

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  const signOutAndRedirect = useCallback(() => {
    router.replace("/login");
  }, [router]);

  // --- boot: who am I, what sessions exist, which was I last on ---
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const { user } = await api.me();
        if (!live) return;
        setUser(user);
        setBooting(false);

        const list = await api.listSessions();
        if (!live) return;
        setSessions(list);
        setSessionsLoading(false);

        const stored =
          typeof window !== "undefined" ? window.localStorage.getItem(ACTIVE_KEY) : null;
        const pick = list.find((s) => s.id === stored) ?? list[0];
        if (pick) setActiveId(pick.id);
      } catch (e) {
        if (!live) return;
        if (isUnauthorized(e)) signOutAndRedirect();
        else {
          setBanner(e instanceof Error ? e.message : "Failed to load");
          setBooting(false);
          setSessionsLoading(false);
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [signOutAndRedirect]);

  // --- session state survives refresh: refetch messages on active change ---
  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    if (typeof window !== "undefined") window.localStorage.setItem(ACTIVE_KEY, activeId);

    let live = true;
    setMessagesLoading(true);
    api
      .listMessages(activeId)
      .then((ms) => {
        if (!live) return;
        setMessages(ms);
        pinnedRef.current = true;
      })
      .catch((e) => {
        if (!live) return;
        if (isUnauthorized(e)) signOutAndRedirect();
        else setBanner(e instanceof Error ? e.message : "Failed to load messages");
      })
      .finally(() => {
        if (live) setMessagesLoading(false);
      });
    return () => {
      live = false;
    };
  }, [activeId, signOutAndRedirect]);

  // --- autoscroll, but only while the user is already at the bottom ---
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }, []);

  useEffect(() => {
    if (!pinnedRef.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    // Segments grow without `messages` changing identity while streaming, so
    // they have to be a dependency or the view stops following the reply.
  }, [messages, stream.segments]);

  // Cancel any in-flight stream on unmount.
  useEffect(() => () => abortRef.current?.abort(), []);

  // --- session CRUD ---

  async function newChat() {
    setDrawerOpen(false);
    try {
      const s = await api.createSession();
      setSessions((prev) => [s, ...prev]);
      setActiveId(s.id);
    } catch (e) {
      setBanner(e instanceof Error ? e.message : "Could not create chat");
    }
  }

  async function renameChat(id: string, title: string) {
    const prev = sessions;
    setSessions((s) => s.map((x) => (x.id === id ? { ...x, title } : x)));
    try {
      await api.renameSession(id, title);
    } catch (e) {
      setSessions(prev); // roll back
      setBanner(e instanceof Error ? e.message : "Rename failed");
    }
  }

  async function deleteChat(id: string) {
    const prev = sessions;
    const remaining = prev.filter((x) => x.id !== id);
    setSessions(remaining);
    if (id === activeId) setActiveId(remaining[0]?.id ?? null);
    try {
      await api.deleteSession(id);
    } catch (e) {
      setSessions(prev);
      setBanner(e instanceof Error ? e.message : "Delete failed");
    }
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      if (typeof window !== "undefined") window.localStorage.removeItem(ACTIVE_KEY);
      signOutAndRedirect();
    }
  }

  // --- the streaming turn ---

  async function send(text: string) {
    let sessionId = activeId;

    // First message with no session yet: create one on the fly.
    if (!sessionId) {
      try {
        const s = await api.createSession(text.slice(0, 48));
        setSessions((prev) => [s, ...prev]);
        setActiveId(s.id);
        sessionId = s.id;
      } catch (e) {
        setBanner(e instanceof Error ? e.message : "Could not start chat");
        return;
      }
    }

    const stamp = new Date().toISOString();
    const userMsg: Message = {
      id: `local-u-${Date.now()}`,
      role: "user",
      content: text,
      created_at: stamp,
    };
    const draftId = `local-a-${Date.now()}`;
    const draft: Message = {
      id: draftId,
      role: "assistant",
      content: "",
      tool_calls: [],
      options: [],
      created_at: stamp,
      pending: true,
    };

    pinnedRef.current = true;
    setMessages((prev) => [...prev, userMsg, draft]);
    setLiveId(draftId);
    stream.begin();
    setStreaming(true);
    setBanner(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const patch = (fn: (m: Message) => Message) =>
      setMessages((prev) => prev.map((m) => (m.id === draftId ? fn(m) : m)));

    /**
     * Settle the bubble: copy the streamed prose onto `content` and the tool
     * calls onto `tool_calls`, so the message looks exactly like one loaded
     * from the transcript. This is what keeps reload working — segments are
     * discarded, and the stored fields are the source of truth from here on.
     */
    const persist = (extra: Partial<Message> = {}) => {
      stream.settle();
      // text()/toolCalls() read the hook's ref, so they are current here even
      // though `stream.segments` (state) is the render-time snapshot.
      const prose = stream.text();
      const tools = stream.toolCalls();
      patch((m) => ({
        ...m,
        content: prose || m.content,
        tool_calls: tools.length ? tools : m.tool_calls,
        pending: false,
        ...extra,
      }));
      setLiveId(null);
    };

    try {
      for await (const ev of api.stream(sessionId, text, ctrl.signal)) {
        // Every event feeds the reducer and re-arms the idle timer, including
        // the ones that produce no segment.
        stream.push(ev);

        switch (ev.type) {
          case "options":
            patch((m) => ({
              ...m,
              options: [...(m.options ?? []), ev.group as OptionGroup],
            }));
            break;

          case "error":
            patch((m) => ({ ...m, error: ev.message }));
            break;

          case "done":
            // Adopt the server id so a later refetch dedupes cleanly. The id
            // change also ends the live view, since liveId no longer matches.
            persist({ id: ev.message_id || draftId });
            break;

          // `start`, `token` and `tool` are fully handled by the reducer.
          default:
            break;
        }
      }
      // Stream ended without an explicit `done` — still settle the bubble.
      persist();
    } catch (e) {
      if (isUnauthorized(e)) {
        stream.settle();
        setLiveId(null);
        signOutAndRedirect();
        return;
      }
      persist({ error: e instanceof Error ? e.message : "Streaming failed" });
    } finally {
      abortRef.current = null;
      setStreaming(false);
      setLiveId(null);
      // Bump this session to the top and pick up any server-side auto-title.
      api
        .listSessions()
        .then(setSessions)
        .catch(() => {});
    }
  }

  function stop() {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    // Keep whatever streamed so far, as stored content, exactly as a finished
    // turn would look — an aborted reply is still worth reading.
    const prose = stream.text();
    const tools = stream.toolCalls();
    stream.settle();
    setMessages((prev) =>
      prev.map((m) =>
        m.pending
          ? {
              ...m,
              content: m.content || prose,
              tool_calls: tools.length ? tools : m.tool_calls,
              pending: false,
            }
          : m,
      ),
    );
    setLiveId(null);
  }

  if (booting) {
    return (
      <main className="grid min-h-dvh place-items-center bg-bg">
        <p className="text-sm text-fg-muted">Loading…</p>
      </main>
    );
  }

  const activeTitle = sessions.find((s) => s.id === activeId)?.title ?? "New chat";

  return (
    <div className="flex h-dvh overflow-hidden bg-bg">
      {/* Sidebar: static column on desktop, slide-over drawer on mobile. */}
      <aside className="hidden w-64 shrink-0 border-r border-border md:block">
        <Sidebar
          sessions={sessions}
          activeId={activeId}
          user={user}
          loading={sessionsLoading}
          onSelect={setActiveId}
          onNew={newChat}
          onRename={renameChat}
          onDelete={deleteChat}
          onLogout={logout}
        />
      </aside>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
          />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85vw] border-r border-border shadow-xl">
            <Sidebar
              sessions={sessions}
              activeId={activeId}
              user={user}
              loading={sessionsLoading}
              onSelect={(id) => {
                setActiveId(id);
                setDrawerOpen(false);
              }}
              onNew={newChat}
              onRename={renameChat}
              onDelete={deleteChat}
              onLogout={logout}
            />
          </div>
        </div>
      )}

      {/* min-w-0 here is load-bearing: without it a wide table in a message
          expands this flex child and pushes the whole layout sideways. */}
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open chats"
            className="grid h-8 w-8 place-items-center rounded-lg hover:bg-surface-2 md:hidden"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M3 6h18M3 12h18M3 18h18" />
            </svg>
          </button>
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium">{activeTitle}</h1>
          {MOCK_MODE && (
            <span className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
              MOCK
            </span>
          )}
        </header>

        {banner && (
          <div role="alert" className="shrink-0 bg-red-500/10 px-4 py-2 text-center text-xs text-red-500">
            {banner}
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="ml-2 underline"
              aria-label="Dismiss"
            >
              dismiss
            </button>
          </div>
        )}

        <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
          {messagesLoading && messages.length === 0 ? (
            <p className="py-10 text-center text-sm text-fg-muted">Loading messages…</p>
          ) : messages.length === 0 ? (
            <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center gap-2 px-6 text-center">
              <h2 className="text-lg font-semibold">Plan a Dubai trip</h2>
              <p className="text-sm text-fg-muted">
                Ask for hotels, tours, transfers or flights. Prices come back in ₹ with a
                comparison table.
              </p>
            </div>
          ) : (
            <MessageList
              messages={messages}
              liveId={liveId}
              segments={stream.segments}
              streaming={streaming}
            />
          )}
        </div>

        <Composer onSend={send} onStop={stop} streaming={streaming} />
      </main>
    </div>
  );
}
