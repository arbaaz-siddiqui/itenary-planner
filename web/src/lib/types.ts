// Shapes from the backend API contract. Anything the contract leaves open
// (option item fields, tool output) is typed loosely on purpose — the agent
// returns supplier payloads whose keys vary by kind.

export interface User {
  id: string;
  email: string;
  /** Empty for accounts created before sign-up asked for a name. */
  first_name?: string;
  last_name?: string;
}

/** Travel defaults from Settings; the agent reads them at the start of a chat. */
export interface Preferences {
  origin_city?: string;
  usual_party_size?: number | null;
  diet?: "" | "Veg" | "Jain" | "Vegan";
  nationality?: string;
}

export interface ChatSession {
  id: string;
  title: string;
  /** Stored on the server with the chat, so it follows the account. */
  pinned?: boolean;
  created_at: string;
  updated_at: string;
}

/** One upstream HTTP call the agent made while running a tool. */
export interface ApiCall {
  url: string;
  status: number;
  duration_ms: number;
  request_body?: unknown;
  response_body?: unknown;
}

export interface ToolCall {
  name: string;
  input?: unknown;
  output?: unknown;
  api_calls?: ApiCall[];
  /** Present on live `tool` frames: the backend emits the start and the result
   *  as two events with the same id, and the UI patches the chip by id.
   *  Absent on stored tool_calls read back from the transcript. */
  id?: string;
  status?: "running" | "done" | "error";
}

export type OptionKind = "tours" | "hotels" | "flights" | "restaurants";

/** An option card. Keys are kind-dependent; see components/cards/Cards.tsx. */
export type OptionItem = Record<string, unknown>;

export interface OptionGroup {
  kind: OptionKind | string;
  items: OptionItem[];
}

/** One section of a variant's detail panel: Inclusions, Exclusions, policies. */
export interface TourDetailSection {
  title: string;
  summary: string;
  items: string[];
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: ToolCall[] | null;
  created_at: string;
  /**
   * Option cards for this message: collected off the stream while it is live,
   * and returned by the transcript on reload. They used to be stream-only, so
   * refreshing the page dropped every card and every tour image with it.
   */
  options?: OptionGroup[];
  /** The customer's thumbs up / down on an assistant reply. */
  feedback?: "up" | "down" | null;
  /** Client-side only: streaming/errored state for the live bubble. */
  pending?: boolean;
  error?: string | null;
}

// --- SSE events from POST /chat/stream ---

export type StreamEvent =
  | { type: "start"; session_id: string }
  | { type: "token"; text: string }
  | { type: "tool"; tool: ToolCall }
  | { type: "options"; group: OptionGroup }
  | { type: "error"; message: string }
  // `content` is the reply as the server stored it; absent from older servers.
  | { type: "done"; message_id: string; content?: string };

// --- Live-stream segments -------------------------------------------------
// While a turn streams we keep an ORDERED list of what happened instead of one
// concatenated string, so a tool call that landed between two paragraphs still
// renders between them. This is a streaming-only construct: once `done` lands
// the message persists as content + tool_calls and reloads from those.

export type Segment =
  | { type: "text"; content: string }
  | { type: "thinking"; content?: string }
  | {
      type: "tool_call";
      id: string;
      name: string;
      args: unknown;
      result?: unknown;
      status: "running" | "done" | "error";
    };

/** Screen preferences stored on the account (server: users.ui_settings). */
export interface UiSettings {
  theme?: "dark" | "light" | "system";
  sidebar_collapsed?: boolean;
}

/** A hearted card as the server returns it. */
export interface SavedCardOut {
  id: string;
  kind: string;
  item: Record<string, unknown>;
  saved_at: string;
}
