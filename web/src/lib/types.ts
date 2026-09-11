// Shapes from the backend API contract. Anything the contract leaves open
// (option item fields, tool output) is typed loosely on purpose — the agent
// returns supplier payloads whose keys vary by kind.

export interface User {
  id: string;
  email: string;
}

export interface ChatSession {
  id: string;
  title: string;
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

export type OptionKind = "tours" | "hotels" | "flights";

/** An option card. Keys are kind-dependent; see components/OptionCards.tsx. */
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
  | { type: "done"; message_id: string };

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
