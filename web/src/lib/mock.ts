// Canned backend used when NEXT_PUBLIC_MOCK_MODE=true, so the whole UI —
// login, sessions, persistence, streaming, tables, images, debug panel — is
// exercisable before the real API exists. State lives in memory plus
// localStorage so a refresh still shows your sessions.

import type {
  ChatSession,
  Message,
  OptionGroup,
  StreamEvent,
  ToolCall,
  User,
} from "./types";

const STORE_KEY = "mock-store-v1";

interface MockStore {
  user: User | null;
  sessions: ChatSession[];
  messages: Record<string, Message[]>;
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

export const mockApi = {
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
    return { user: s.user };
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
