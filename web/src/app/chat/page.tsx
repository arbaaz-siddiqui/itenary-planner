"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, Menu } from "lucide-react";
import { api, isUnauthorized, MOCK_MODE } from "@/lib/api";
import type { ChatSession, Message, OptionGroup, User } from "@/lib/types";
import { useStreamSegments } from "@/lib/useStreamSegments";
import { usePersisted } from "@/lib/usePersisted";
import { useTheme } from "@/lib/theme";
import { displayFirstName, serviceById, type ServiceId } from "@/lib/services";
import { Sidebar } from "@/components/Sidebar";
import { MessageList } from "@/components/MessageList";
import { Composer, type ComposerHandle, type ComposerMode } from "@/components/Composer";
import { HomeHero } from "@/components/HomeHero";
import { SearchDialog } from "@/components/SearchDialog";
import { SettingsDialog } from "@/components/SettingsDialog";
import { NameDialog } from "@/components/NameDialog";
import { DetailPanel } from "@/components/detail/DetailPanel";
import { CardActionsProvider, type CardActions, type CardRef } from "@/lib/cardActions";
import { formatInr, num, titleOf } from "@/lib/cards";
import { toCardRefs } from "@/lib/cardRefs";

const ACTIVE_KEY = "active-session-id";
const WAITING_ID = "local-waiting";
const WAITING_SEGMENTS = [{ type: "thinking" as const, content: "Still working on your answer" }];

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

  // Shell state. Collapsed and pinned are per-browser conveniences.
  const [collapsed, setCollapsed] = usePersisted("sidebar-collapsed", false);
  const [pinnedList, setPinnedList] = usePersisted<string[]>("pinned-chats", []);
  const pinned = useMemo(() => new Set(pinnedList), [pinnedList]);
  const [service, setService] = useState<ServiceId | null>(null);
  const [mode, setMode] = useState<ComposerMode>("ask");
  const [searchOpen, setSearchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const theme = useTheme();
  const composerRef = useRef<ComposerHandle>(null);

  // Cards: saved with the heart (per browser), and the one open in detail.
  const [saved, setSaved] = usePersisted<CardRef[]>("saved-cards", []);
  const [detail, setDetail] = useState<CardRef | null>(null);
  const detailPushed = useRef(false);
  const [nameLater, setNameLater] = useState(false);
  /** False once the reader scrolls up during a reply: show "Jump to latest". */
  const [atBottom, setAtBottom] = useState(true);
  /** True for the moment the home screen hands over to the chat. */
  const [entering, setEntering] = useState(false);
  /** A chat whose reply is still being worked on, by a turn this tab left. */
  const [waitingFor, setWaitingFor] = useState<string | null>(null);

  // Live segments + the idle "Thinking…" timer. Segments are a streaming-only
  // view; the settled message still persists as content + tool_calls.
  const stream = useStreamSegments();

  const abortRef = useRef<AbortController | null>(null);
  /**
   * A chat created by its own first message. Its history is the message being
   * streamed right now, so loading it from the server would replace the live
   * reply with an empty list -- which is how the first message of a new chat
   * appeared to get no answer at all.
   */
  const createdHereRef = useRef<string | null>(null);
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
    if (createdHereRef.current === activeId) {
      createdHereRef.current = null;
      return;
    }

    let live = true;
    setMessagesLoading(true);
    api
      .listMessages(activeId)
      .then(async (ms) => {
        if (!live) return;
        setMessages(ms);
        pinnedRef.current = true;
        // A reply that kept running while the customer was in another chat.
        const { busy } = await api.sessionStatus(activeId).catch(() => ({ busy: false }));
        if (live && busy) setWaitingFor(activeId);
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
    setAtBottom(pinnedRef.current);
  }, []);

  const jumpToLatest = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = true;
    setAtBottom(true);
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, []);

  // Content that fits the view leaves nothing to jump to: a short chat, or a
  // switch from a long one, must not keep a stale jump arrow.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || el.scrollHeight - el.clientHeight >= 120) return;
    pinnedRef.current = true;
    setAtBottom(true);
  }, [activeId, messages, stream.segments]);

  useEffect(() => {
    if (!pinnedRef.current) return;
    const el = scrollRef.current;
    // Smooth, so new text glides into view instead of jumping.
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    // Segments grow without `messages` changing identity while streaming, so
    // they have to be a dependency or the view stops following the reply.
  }, [messages, stream.segments]);

  // Poll a chat whose reply is still running, and load it when it lands.
  useEffect(() => {
    if (!waitingFor) return;
    if (waitingFor !== activeId) {
      setWaitingFor(null);
      return;
    }
    let live = true;
    const timer = window.setInterval(async () => {
      const { busy } = await api.sessionStatus(waitingFor).catch(() => ({ busy: true }));
      if (!live || busy) return;
      const ms = await api.listMessages(waitingFor).catch(() => null);
      if (!live) return;
      if (ms) setMessages(ms);
      setWaitingFor(null);
    }, 2500);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [waitingFor, activeId]);

  // Cancel any in-flight stream on unmount.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Ctrl/Cmd+K opens chat search from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // --- cards: detail view (URL-synced), saving, booking ---

  const openCard = useCallback((card: CardRef) => {
    setDetail(card);
    // ?view=kind&id=... so the view can be shared and Back closes it.
    const url = new URL(window.location.href);
    url.searchParams.set("view", card.kind);
    url.searchParams.set("id", card.id);
    if (detailPushed.current) window.history.replaceState({ detail: true }, "", url);
    else window.history.pushState({ detail: true }, "", url);
    detailPushed.current = true;
  }, []);

  const closeCard = useCallback(() => {
    setDetail(null);
    if (detailPushed.current) {
      detailPushed.current = false;
      window.history.back();
    } else {
      const url = new URL(window.location.href);
      url.searchParams.delete("view");
      url.searchParams.delete("id");
      window.history.replaceState(null, "", url);
    }
  }, []);

  // Browser Back closes the panel instead of leaving the page.
  useEffect(() => {
    const onPop = () => {
      detailPushed.current = false;
      setDetail(null);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // A shared link (?view=&id=) reopens its card once the chat or the saved
  // list has it.
  useEffect(() => {
    if (detail) return;
    const params = new URLSearchParams(window.location.search);
    const id = params.get("id");
    if (!params.get("view") || !id) return;
    const fromChat = messages.flatMap((m) =>
      (m.options ?? [])
        .filter((g) => g.kind !== "suggestions" && g.kind !== "itinerary_pdf")
        .flatMap((g) => toCardRefs(g.items ?? [], g.kind)),
    );
    const found = [...fromChat, ...saved].find((c) => c.id === id);
    if (found) setDetail(found);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, saved]);

  function bookCard(card: CardRef) {
    // The selection goes to the agent as the customer's next message, so the
    // conversation continues from exactly what they picked.
    if (detail) closeCard();
    const price = formatInr(
      num(card.item, "price_inr") ?? num(card.item, "price_per_adult_inr") ?? num(card.item, "price_per_person_inr"),
    );
    const what = titleOf(card.item, card.kind) + (price ? " (" + price + ")" : "");
    void send("I would like to book " + what + ". What do you need from me to confirm it?");
  }

  const cardActions: CardActions = {
    open: openCard,
    isSaved: (id) => saved.some((c) => c.id === id),
    toggleSave: (card) =>
      setSaved((list) =>
        list.some((c) => c.id === card.id) ? list.filter((c) => c.id !== card.id) : [card, ...list].slice(0, 50),
      ),
    book: bookCard,
  };

  function askAbout(card: CardRef) {
    closeCard();
    // Names the kind and the exact title, so the agent ties the question to
    // the option it returned under that name.
    composerRef.current?.prefill("About the " + card.kind + " " + titleOf(card.item, card.kind) + ": ");
  }

  /** Send the last question again, replacing the reply it got. */
  function regenerate() {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser || streaming) return;
    // Keep the question, drop the reply it got; the server does the same.
    setMessages((prev) => {
      const i = prev.findIndex((m) => m.id === lastUser.id);
      return i >= 0 ? prev.slice(0, i + 1) : prev;
    });
    void send(lastUser.content, { regenerate: true });
  }

  /** Download the open chat as plain text. */
  function exportChat() {
    if (!messages.length) return;
    const title = sessions.find((x) => x.id === activeId)?.title ?? "GujjuTours chat";
    const body = messages
      .map((m) => (m.role === "user" ? "You" : "GujjuTours AI") + ":\n" + m.content.trim())
      .join("\n\n");
    const blob = new Blob([title + "\n\n" + body + "\n"], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = title.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").slice(0, 60) + ".txt";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  /** Delete every chat. Throws if any delete failed, so Settings can say so. */
  async function clearAllChats() {
    if (streaming) stop();
    const results = await Promise.allSettled(sessions.map((x) => api.deleteSession(x.id)));
    const failed = sessions.filter((_, i) => results[i].status === "rejected");
    setSessions(failed);
    setPinnedList([]);
    setActiveId(null);
    setMessages([]);
    if (failed.length) throw new Error(failed.length + " chats were not deleted");
  }

  // --- session CRUD ---

  /**
   * Back to the home screen. The session itself is created by the first
   * message, so opening "New chat" and walking away leaves no empty chat.
   */
  function newChat(withService: ServiceId | null = null) {
    if (streaming) detach();
    setDrawerOpen(false);
    setActiveId(null);
    setMessages([]);
    setService(withService);
  }

  function selectChat(id: string) {
    if (id === activeId) {
      setDrawerOpen(false);
      setSearchOpen(false);
      return;
    }
    if (streaming) detach();
    setDrawerOpen(false);
    setSearchOpen(false);
    setService(null);
    setActiveId(id);
  }

  function togglePin(id: string) {
    setPinnedList((list) => (list.includes(id) ? list.filter((x) => x !== id) : [id, ...list]));
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
    setPinnedList((list) => list.filter((x) => x !== id));
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

  async function send(typed: string, opts: { regenerate?: boolean } = {}) {
    // A service picked in the sidebar names the intent for the agent, once.
    const svc = opts.regenerate ? null : serviceById(service);
    const text =
      svc && !typed.toLowerCase().includes(svc.label.toLowerCase()) ? `${svc.label}: ${typed}` : typed;
    // Leaving the home screen: the gradient fades into the chat instead of
    // cutting to it.
    if (messages.length === 0) {
      setEntering(true);
      window.setTimeout(() => setEntering(false), 700);
    }
    setService(null);
    let sessionId = activeId;

    // First message with no session yet: create one on the fly.
    if (!sessionId) {
      try {
        const s = await api.createSession(typed.slice(0, 48));
        setSessions((prev) => [s, ...prev]);
        createdHereRef.current = s.id;
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
    // A regenerated answer reuses the question already on screen.
    setMessages((prev) => (opts.regenerate ? [...prev, draft] : [...prev, userMsg, draft]));
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
      for await (const ev of api.stream(sessionId, text, ctrl.signal, mode, Boolean(opts.regenerate))) {
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
            // The server's stored reply wins over the streamed text: it is the
            // corrected version when a guard rewrote the answer.
            persist({
              id: ev.message_id || draftId,
              ...(ev.content ? { content: ev.content } : {}),
            });
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

  /**
   * Leave a reply running on the server while the customer looks elsewhere.
   * Only this tab stops listening; the turn finishes and is saved, and the
   * chat shows it when they come back.
   */
  function detach() {
    abortRef.current?.abort();
    abortRef.current = null;
    stream.settle();
    setStreaming(false);
    setLiveId(null);
  }

  function stop() {
    if (activeId) void api.cancelTurn(activeId).catch(() => {});
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

  const name = displayFirstName(user);
  const home = !messagesLoading && messages.length === 0;

  const sidebarProps = {
    sessions,
    activeId,
    user,
    loading: sessionsLoading,
    pinned,
    activeService: service,
    onSelect: selectChat,
    onNew: () => newChat(),
    onService: (id: ServiceId) => newChat(id),
    onSearch: () => {
      setDrawerOpen(false);
      setSearchOpen(true);
    },
    onRename: renameChat,
    onDelete: deleteChat,
    onTogglePin: togglePin,
    onSettings: () => {
      setDrawerOpen(false);
      setSettingsOpen(true);
    },
    onLogout: logout,
    themeResolved: theme.resolved,
    saved,
    onOpenSaved: (c: CardRef) => {
      setDrawerOpen(false);
      openCard(c);
    },
    onToggleTheme: () => theme.setPref(theme.resolved === "dark" ? "light" : "dark"),
  };

  const composer = (variant: "hero" | "dock") => (
    <Composer
      ref={composerRef}
      variant={variant}
      onSend={send}
      onStop={stop}
      streaming={streaming}
      service={service}
      onClearService={() => setService(null)}
      mode={mode}
      onModeChange={setMode}
    />
  );

  return (
    <CardActionsProvider value={cardActions}>
    <div className="flex h-dvh overflow-hidden bg-bg">
      {/* Desktop: a 280px column that collapses to a 64px icon rail. */}
      <aside
        className={`hidden shrink-0 border-r border-sidebar-border transition-[width] duration-200 ease-out lg:block ${
          collapsed ? "w-16" : "w-[280px]"
        }`}
      >
        <Sidebar {...sidebarProps} collapsed={collapsed} onToggleCollapse={() => setCollapsed((c) => !c)} />
      </aside>

      {/* Below 1024px: an off-canvas drawer. */}
      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setDrawerOpen(false)} aria-hidden="true" />
          <div className="absolute inset-y-0 left-0 w-[280px] max-w-[85vw] shadow-2xl">
            <Sidebar {...sidebarProps} onClose={() => setDrawerOpen(false)} />
          </div>
        </div>
      )}

      {/* min-w-0 here is load-bearing: without it a wide table in a message
          expands this flex child and pushes the whole layout sideways. */}
      <main className="relative flex min-w-0 flex-1 flex-col p-2">
        {/* No top bar, as in the reference layout. Below 1024px the sidebar is
            a drawer, so the menu button floats over the page instead. */}
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open menu"
          className="absolute left-3 top-3 z-30 grid h-10 w-10 place-items-center rounded-xl border border-border bg-surface/80 text-fg backdrop-blur hover:bg-surface-2 lg:hidden"
        >
          <Menu size={19} aria-hidden />
        </button>
        {MOCK_MODE && (
          <span className="absolute right-3 top-3 z-30 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
            MOCK
          </span>
        )}

        {banner && (
          <div role="alert" className="shrink-0 bg-brand-red/10 px-4 py-2 text-center text-xs text-brand-red">
            {banner}
            <button type="button" onClick={() => setBanner(null)} className="ml-2 underline" aria-label="Dismiss">
              dismiss
            </button>
          </div>
        )}

        {/* One rounded panel for both states: the gradient on the home
            screen, a dark (or light) canvas with a low brand glow in chat. */}
        <div
          className={`relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-[22px] ${home ? "" : "chat-canvas border border-border"}`}
        >
          {/* The home gradient fading out over the chat as it arrives. */}
          {entering && !home && <div className="hero-glow glow-out pointer-events-none absolute inset-0 z-10" aria-hidden />}
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className={`no-scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto ${entering && !home ? "chat-enter" : ""}`}
          >
            {messagesLoading && messages.length === 0 ? (
              <div className="mx-auto w-full max-w-[820px] space-y-4 px-4 py-10" role="status" aria-label="Loading messages">
                <div className="skeleton ml-auto h-10 w-1/3 rounded-2xl" />
                <div className="skeleton h-4 w-3/4 rounded" />
                <div className="skeleton h-4 w-2/3 rounded" />
              </div>
            ) : home ? (
              <HomeHero name={name} service={service} onPick={(p) => send(p)}>
                {composer("hero")}
              </HomeHero>
            ) : (
              <MessageList
                messages={
                  waitingFor === activeId
                    ? [...messages, { id: WAITING_ID, role: "assistant", content: "", created_at: "", pending: true }]
                    : messages
                }
                liveId={waitingFor === activeId ? WAITING_ID : liveId}
                segments={waitingFor === activeId ? WAITING_SEGMENTS : stream.segments}
                streaming={streaming || waitingFor === activeId}
                onSuggest={(t) => send(t)}
                onRegenerate={regenerate}
                onEdit={(t) => composerRef.current?.prefill(t)}
                onRate={(id, v) => api.rateMessage(id, v)}
              />
            )}
          </div>

          {!home && !atBottom && (
            <button
              type="button"
              onClick={jumpToLatest}
              aria-label="Jump to latest"
              title="Jump to latest"
              className="press absolute right-5 bottom-32 z-20 grid h-10 w-10 place-items-center rounded-full border border-border bg-surface text-fg shadow-lg hover:bg-surface-2"
            >
              <ArrowDown size={18} aria-hidden />
            </button>
          )}

          {!home && <div className={entering ? "dock-enter" : ""}>{composer("dock")}</div>}
        </div>
      </main>

      <SearchDialog open={searchOpen} sessions={sessions} onPick={selectChat} onClose={() => setSearchOpen(false)} />
      <DetailPanel card={detail} onClose={closeCard} onBook={bookCard} onAsk={askAbout} />
      <NameDialog
        open={Boolean(user && !user.first_name && !nameLater && !MOCK_MODE)}
        onSaved={setUser}
        onLater={() => setNameLater(true)}
        onSignedOut={signOutAndRedirect}
      />
      <SettingsDialog
        open={settingsOpen}
        user={user}
        onUser={setUser}
        theme={theme.pref}
        onTheme={theme.setPref}
        onExport={exportChat}
        onClearAll={clearAllChats}
        canExport={messages.length > 0}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
    </CardActionsProvider>
  );
}
