// Canned backend used when NEXT_PUBLIC_MOCK_MODE=true, so the whole UI —
// login, sessions, persistence, streaming, tables, images, debug panel — is
// exercisable before the real API exists. State lives in memory plus
// localStorage so a refresh still shows your sessions.

import type {
  AdminOverview,
  AdminSearch,
  AdminSearchList,
  AdminTranscript,
  AdminUserDetail,
  AdminUserList,
  SearchFilters,
} from "./admin";
import type {
  ChatSession,
  Message,
  OptionGroup,
  SavedCardOut,
  StreamEvent,
  ToolCall,
  UiSettings,
  User,
} from "./types";

const STORE_KEY = "mock-store-v1";

interface MockStore {
  user: User | null;
  sessions: ChatSession[];
  messages: Record<string, Message[]>;
  saved?: SavedCardOut[];
  settings?: UiSettings;
}

const MOCK_USER: User = { id: "u_1", email: "demo@example.com" };

function nowISO(): string {
  return new Date().toISOString();
}

function freshStore(): MockStore {
  const id = "s_seed";
  const t = nowISO();
  return {
    user: null,
    sessions: [
      { id, title: "Dubai 5 nights, family of 4", created_at: t, updated_at: t },
    ],
    messages: {
      [id]: [
        {
          id: "m_1",
          role: "user",
          content: "We want 5 nights in Dubai in December, 2 adults 2 kids. Show me some 4-star hotels.",
          created_at: t,
        },
        {
          id: "m_2",
          role: "assistant",
          content: SEED_ASSISTANT_MD,
          tool_calls: [SEED_TOOL_CALL],
          options: [SEED_HOTEL_OPTIONS],
          created_at: t,
        },
      ],
    },
  };
}

function load(): MockStore {
  if (typeof window === "undefined") return freshStore();
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (raw) return JSON.parse(raw) as MockStore;
  } catch {
    /* corrupt or unavailable storage — start clean */
  }
  const s = freshStore();
  save(s);
  return s;
}

function save(s: MockStore): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(s));
  } catch {
    /* quota or private mode — mock state is best-effort */
  }
}

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

let store: MockStore | null = null;
function db(): MockStore {
  if (!store) store = load();
  seedSeq(store);
  return store;
}
function commit(): void {
  if (store) save(store);
}

// Ids must not collide with ones already in the persisted store. A plain
// counter restarts at 100 on every reload and re-issues m_101, m_102, … on top
// of messages saved before the reload, which React then reports as duplicate
// keys. Seed the counter past whatever the store already holds.
let seq = 100;
let seqSeeded = false;

function seedSeq(s: MockStore): void {
  if (seqSeeded) return;
  seqSeeded = true;
  const ids = [
    ...s.sessions.map((x) => x.id),
    ...Object.values(s.messages).flatMap((ms) => ms.map((m) => m.id)),
  ];
  for (const id of ids) {
    const n = Number.parseInt(id.split("_")[1] ?? "", 10);
    if (Number.isFinite(n) && n > seq) seq = n;
  }
}

const nextId = (p: string) => `${p}_${++seq}`;

// --- admin panel stubs: built from the mock store, read-only -------------

function mockSearches(s: MockStore): AdminSearch[] {
  const user = s.user ?? MOCK_USER;
  return Object.entries(s.messages).flatMap(([sid, ms]) =>
    ms.flatMap((m) =>
      (m.tool_calls ?? []).map((tc, i) => {
        const input = (tc.input ?? {}) as Record<string, unknown>;
        const output = (tc.output ?? {}) as Record<string, unknown>;
        return {
          id: `${m.id}_${i}`,
          user_id: user.id,
          email: user.email,
          session_id: sid,
          tool: tc.name,
          destination: typeof input.destination_city === "string" ? input.destination_city : null,
          start_date: typeof input.check_in === "string" ? input.check_in : null,
          party: typeof input.adults === "number" ? `${input.adults} adults` : null,
          result_count: Array.isArray(output.options) ? output.options.length : null,
          error: output.error ? String(output.message ?? "failed") : null,
          created_at: m.created_at,
        };
      }),
    ),
  );
}

const mockAdmin = {
  async adminOverview(days: number): Promise<AdminOverview> {
    await delay(200);
    const s = db();
    const searches = mockSearches(s);
    const turns = Object.values(s.messages).flat().filter((m) => m.role === "user").length;
    const today = new Date();
    const daily = Array.from({ length: days }, (_, i) => {
      const d = new Date(today.getTime() - (days - 1 - i) * 86_400_000);
      const last = i === days - 1;
      return {
        day: d.toISOString().slice(0, 10),
        active_users: last ? 1 : 0,
        signups: 0,
        turns: last ? turns : 0,
        searches: last ? searches.length : 0,
        failed_searches: 0,
      };
    });
    const byTool = new Map<string, number>();
    for (const r of searches) byTool.set(r.tool, (byTool.get(r.tool) ?? 0) + 1);
    return {
      days,
      kpis: {
        total_users: 1,
        new_users: 0,
        active_users: 1,
        chats: s.sessions.length,
        turns,
        searches: searches.length,
        failed_searches: searches.filter((r) => r.error).length,
        thumbs_up: 0,
        thumbs_down: 0,
      },
      daily,
      searches_by_tool: [...byTool].map(([tool, n]) => ({ tool, searches: n, failed: 0 })),
      top_destinations: searches.some((r) => r.destination)
        ? [{ destination: "Dubai", searches: searches.filter((r) => r.destination).length }]
        : [],
      recent_failed: searches.filter((r) => r.error),
    };
  },

  async adminUsers(_q: string, page: number, pageSize: number): Promise<AdminUserList> {
    await delay(200);
    const s = db();
    const u = s.user ?? MOCK_USER;
    const all = Object.values(s.messages).flat();
    return {
      total: 1,
      page,
      page_size: pageSize,
      users: [
        {
          id: u.id,
          email: u.email,
          first_name: u.first_name ?? "",
          last_name: u.last_name ?? "",
          created_at: s.sessions[s.sessions.length - 1]?.created_at ?? nowISO(),
          last_active: all[all.length - 1]?.created_at ?? null,
          chats: s.sessions.length,
          messages: all.length,
          searches: mockSearches(s).length,
        },
      ],
    };
  },

  async adminUser(id: string): Promise<AdminUserDetail> {
    await delay(200);
    const s = db();
    const u = s.user ?? MOCK_USER;
    const searches = mockSearches(s);
    return {
      user: { id, email: u.email, first_name: u.first_name ?? "", last_name: u.last_name ?? "", created_at: nowISO() },
      facts: {},
      saved_cards: s.saved?.length ?? 0,
      searches: searches.length,
      last_search: searches[searches.length - 1]?.created_at ?? null,
      last_active: s.sessions[0]?.updated_at ?? null,
      sessions: s.sessions.map((x) => ({
        id: x.id,
        title: x.title,
        pinned: Boolean(x.pinned),
        created_at: x.created_at,
        updated_at: x.updated_at,
        messages: s.messages[x.id]?.length ?? 0,
        searches: searches.filter((r) => r.session_id === x.id).length,
      })),
    };
  },

  async adminTranscript(sessionId: string): Promise<AdminTranscript> {
    await delay(200);
    const s = db();
    const u = s.user ?? MOCK_USER;
    const session = s.sessions.find((x) => x.id === sessionId);
    if (!session) throw Object.assign(new Error("Not Found"), { status: 404 });
    return {
      session: { id: session.id, title: session.title, created_at: session.created_at, updated_at: session.updated_at },
      user: { id: u.id, email: u.email, first_name: u.first_name ?? "", last_name: u.last_name ?? "" },
      messages: (s.messages[sessionId] ?? []).map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        created_at: m.created_at,
        options: m.options ?? [],
        feedback: m.feedback ?? null,
        tool_calls: (m.tool_calls ?? []).map((tc) => ({
          tool_name: tc.name,
          input: tc.input ?? {},
          output_preview: JSON.stringify(tc.output ?? null).slice(0, 2000),
        })),
      })),
    };
  },

  async adminSearches(f: SearchFilters): Promise<AdminSearchList> {
    await delay(200);
    const all = mockSearches(db());
    const rows = all.filter(
      (r) => (!f.tool || r.tool === f.tool) && (!f.errors_only || r.error) && (!f.user_id || r.user_id === f.user_id),
    );
    return { total: rows.length, page: 1, page_size: 50, searches: rows, tools: [...new Set(all.map((r) => r.tool))] };
  },
};

export const mockApi = {
  ...mockAdmin,
  async login(email: string, password: string): Promise<{ user: User }> {
    await delay(400);
    if (!email || !password) {
      throw Object.assign(new Error("Email and password are required"), { status: 400 });
    }
    if (password === "wrong") {
      throw Object.assign(new Error("Invalid email or password"), { status: 401 });
    }
    const s = db();
    s.user = { ...MOCK_USER, email };
    commit();
    return { user: s.user };
  },

  async logout(): Promise<void> {
    await delay(150);
    db().user = null;
    commit();
  },

  async me(): Promise<{ user: User }> {
    await delay(200);
    const s = db();
    if (!s.user) throw Object.assign(new Error("Not authenticated"), { status: 401 });
    // Mock mode shows the admin panel too, over this browser's own mock chats.
    return { user: { ...s.user, is_admin: true } };
  },

  async listSessions(): Promise<ChatSession[]> {
    await delay(200);
    return [...db().sessions].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  },

  async createSession(title?: string): Promise<ChatSession> {
    await delay(200);
    const t = nowISO();
    const s: ChatSession = {
      id: nextId("s"),
      title: title || "New chat",
      created_at: t,
      updated_at: t,
    };
    db().sessions.unshift(s);
    db().messages[s.id] = [];
    commit();
    return s;
  },

  async updateSession(id: string, patch: { title?: string; pinned?: boolean }): Promise<ChatSession> {
    await delay(100);
    const s = db().sessions.find((x) => x.id === id);
    if (!s) throw Object.assign(new Error("Session not found"), { status: 404 });
    Object.assign(s, patch);
    commit();
    return s;
  },

  async listSavedCards(): Promise<{ cards: SavedCardOut[] }> {
    await delay(80);
    return { cards: db().saved ?? [] };
  },

  async saveCard(id: string, kind: string, item: Record<string, unknown>): Promise<void> {
    await delay(80);
    const s = db();
    s.saved = [{ id, kind, item, saved_at: nowISO() }, ...(s.saved ?? []).filter((c) => c.id !== id)].slice(0, 50);
    commit();
  },

  async unsaveCard(id: string): Promise<void> {
    await delay(80);
    const s = db();
    s.saved = (s.saved ?? []).filter((c) => c.id !== id);
    commit();
  },

  async getSettings(): Promise<{ settings: UiSettings }> {
    await delay(50);
    return { settings: db().settings ?? {} };
  },

  async setSettings(patch: UiSettings): Promise<{ settings: UiSettings }> {
    await delay(50);
    const s = db();
    s.settings = { ...(s.settings ?? {}), ...patch };
    commit();
    return { settings: s.settings };
  },

  async renameSession(id: string, title: string): Promise<ChatSession> {
    await delay(150);
    const s = db().sessions.find((x) => x.id === id);
    if (!s) throw Object.assign(new Error("Session not found"), { status: 404 });
    s.title = title;
    s.updated_at = nowISO();
    commit();
    return s;
  },

  async deleteSession(id: string): Promise<void> {
    await delay(150);
    const s = db();
    s.sessions = s.sessions.filter((x) => x.id !== id);
    delete s.messages[id];
    commit();
  },

  async listMessages(id: string): Promise<Message[]> {
    await delay(250);
    return db().messages[id] ?? [];
  },

  /** Fake SSE: tokens dribble out, with a tool event and option cards first. */
  async *stream(
    sessionId: string,
    message: string,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamEvent> {
    const s = db();
    s.messages[sessionId] ??= [];
    s.messages[sessionId].push({
      id: nextId("m"),
      role: "user",
      content: message,
      created_at: nowISO(),
    });
    const sess = s.sessions.find((x) => x.id === sessionId);
    if (sess) {
      sess.updated_at = nowISO();
      // Mimic the backend auto-titling a fresh chat from the first message.
      if (sess.title === "New chat") sess.title = message.slice(0, 48);
    }
    commit();

    // Latency before the first token — this is what the typing indicator is for.
    await delay(900);
    if (signal?.aborted) return;

    // Mirror the real backend: a call is announced when it STARTS (id +
    // input, status running) and patched when it RETURNS (same id + output,
    // status done). The gap between the two is what the spinner is for, and
    // the quiet before the first one is what "Thinking…" is for.
    const { api_calls: _seedCalls, ...seedTool } = SEED_TOOL_CALL;
    yield {
      type: "tool",
      tool: { id: "call_1", name: seedTool.name, input: seedTool.input, status: "running" },
    };
    await delay(2200); // longer than QUIET_GAP_MS, so the idle timer fires
    if (signal?.aborted) return;
    yield {
      type: "tool",
      tool: { id: "call_1", name: seedTool.name, output: seedTool.output, status: "done" },
    };

    // Two adjacent same-name calls, to exercise the "×2" render-time collapse.
    await delay(200);
    yield { type: "tool", tool: { id: "call_2", name: "search_tours", input: { city: "Dubai" }, status: "running" } };
    yield { type: "tool", tool: { id: "call_3", name: "search_tours", input: { city: "Dubai", day: 2 }, status: "running" } };
    await delay(900);
    if (signal?.aborted) return;
    // Results out of order — call_3 lands before call_2, as parallel tools do.
    yield { type: "tool", tool: { id: "call_3", name: "search_tours", output: { count: 6 }, status: "done" } };
    await delay(300);
    yield { type: "tool", tool: { id: "call_2", name: "search_tours", output: { count: 4 }, status: "done" } };

    await delay(300);
    yield { type: "options", group: SEED_HOTEL_OPTIONS };
    await delay(200);

    const reply = MOCK_REPLY_MD;
    // Chunk on word boundaries so markdown tables don't flicker mid-pipe.
    const chunks = reply.match(/\S+\s*/g) ?? [reply];
    let acc = "";
    for (const c of chunks) {
      if (signal?.aborted) return;
      acc += c;
      yield { type: "token", text: c };
      await delay(12);
    }

    const id = nextId("m");
    s.messages[sessionId].push({
      id,
      role: "assistant",
      content: acc,
      tool_calls: [SEED_TOOL_CALL],
      options: [SEED_HOTEL_OPTIONS],
      created_at: nowISO(),
    });
    commit();
    yield { type: "done", message_id: id };
  },
};

// --- canned payloads ---

const SEED_HOTEL_OPTIONS: OptionGroup = {
  kind: "hotels",
  items: [
    {
      hotel_name: "Rove Downtown",
      stars: 3,
      area: "Downtown Dubai",
      price_inr: 68400,
      per_night_inr: 13680,
      nights: 5,
      cheapest_room_type: "Rove Room, 2 Queen Beds",
      cheapest_board: "Room Only",
      cancellation_display: "Free cancellation until 2 Dec",
      has_free_cancellation: true,
      image_urls: ["https://images.unsplash.com/photo-1566073771259-6a8506099945?w=600"],
    },
    {
      hotel_name: "Hilton Garden Inn Al Mina",
      stars: 4,
      area: "Al Mina",
      price_inr: 82150,
      per_night_inr: 16430,
      nights: 5,
      cheapest_room_type: "King Guest Room",
      cheapest_board: "Bed & Breakfast",
      cancellation_display: "Non-refundable",
      has_free_cancellation: false,
      // Deliberately broken URL — proves the image fallback works.
      image_urls: ["https://stagingapi.gujjutours.com/media/missing-hotel.jpg"],
    },
    {
      hotel_name: "Media One Hotel",
      stars: 4,
      area: "Dubai Media City",
      price_inr: 79900,
      per_night_inr: 15980,
      nights: 5,
      cheapest_room_type: "Cosy Room",
      cheapest_board: "Bed & Breakfast",
      image_urls: [],
    },
  ],
};

const SEED_TOOL_CALL: ToolCall = {
  name: "search_hotels",
  input: {
    city: "Dubai",
    check_in: "2026-12-12",
    check_out: "2026-12-17",
    rooms: [{ adults: 2, children: 2, children_ages: [7, 10] }],
    star_rating: [3, 4],
    currency: "INR",
  },
  output: {
    count: 3,
    results: SEED_HOTEL_OPTIONS.items,
    roe: { buyingROE: 23.41, sellingROE: 23.9, base_currency: "AED" },
  },
  api_calls: [
    {
      url: "https://stagingapi.gujjutours.com/api/v1/hotel/search",
      status: 200,
      duration_ms: 4128,
      request_body: {
        cityId: 115936,
        checkIn: "2026-12-12",
        checkOut: "2026-12-17",
        occupancy: [{ adults: 2, children: 2, childAges: [7, 10] }],
      },
      response_body: {
        status: "success",
        hotels: SEED_HOTEL_OPTIONS.items,
        note: "truncated in mock mode; real payloads run 200KB+",
      },
    },
    {
      url: "https://stagingapi.gujjutours.com/api/v1/currency/roe",
      status: 200,
      duration_ms: 212,
      request_body: { from: "AED", to: "INR" },
      response_body: { buyingROE: 23.41, sellingROE: 23.9 },
    },
  ],
};

const MOCK_REPLY_MD = `Here are the best 4-star options for **12–17 Dec** (2 adults + 2 children, 5 nights).

| Hotel | Stars | Area | Board | Per night | Total (5N) | Cancellation |
|---|---|---|---|---|---|---|
| Rove Downtown | 3★ | Downtown Dubai | Room Only | ₹13,680 | **₹68,400** | Free until 2 Dec |
| Media One Hotel | 4★ | Dubai Media City | B&B | ₹15,980 | **₹79,900** | Free until 5 Dec |
| Hilton Garden Inn Al Mina | 4★ | Al Mina | B&B | ₹16,430 | **₹82,150** | Non-refundable |

**My pick:** Media One Hotel — it is the cheapest 4-star *with breakfast included* and
free cancellation, and the Metro is a 6-minute walk.

A few things worth knowing:

- Rove Downtown is 3-star but the closest to Burj Khalifa (0.9 km).
- All three quote in AED; totals above are converted at the buying ROE of 23.41.
- Children 7 and 10 stay free in existing bedding at all three properties.

Want me to add tours next, or hold these and price flights first?

### Tours for day 2

| Tour | Price/adult | Type | Duration | Transfer | Cancellation | Start times |
|---|---|---|---|---|---|---|
| ⭐ Premium Red Dune Desert Safari: BBQ Dinner, Belly Dance, Sandboarding & Camel Ride with 4WD Pickup | ₹3,661 | Shared | 6h 30m | Sharing Transfer: ₹1,793 · Without Transfer: Included (₹0) · Private Transfer: ₹11,452 | Free until 24h before | 14:30, 15:00, 15:30 |
| Burj Khalifa At The Top (Levels 124 & 125) Non-Prime Hours | ₹2,410 | Ticket | 1h 30m | Without Transfer: Included (₹0) · Sharing Transfer: ₹1,793 | Non-refundable | 33 half-hourly slots from 09:00 |
| Dubai Frame Entry Ticket | ₹1,180 | Ticket | 1h | Without Transfer: Included (₹0) | Free until 48h before | 09:00 – 20:00 |

A glossary, which must stay a plain table:

| Term | Meaning |
|---|---|
| B&B | Bed and breakfast included |
| ROE | Rate of exchange |
`;

const SEED_ASSISTANT_MD = `I found 3 properties that fit. Full comparison:

| Hotel | Stars | Total (5N) | Board |
|---|---|---|---|
| Rove Downtown | 3★ | ₹68,400 | Room Only |
| Media One Hotel | 4★ | ₹79,900 | B&B |
| Hilton Garden Inn Al Mina | 4★ | ₹82,150 | B&B |

Tell me which one to hold and I'll move on to tours.`;
