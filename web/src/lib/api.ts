// THE single network boundary. Every call the UI makes goes through here.
// Swapping mock -> real, or changing routes/auth, is a one-file change.

import { mockApi } from "./mock";
import { parseSSE } from "./sse";
import type { ChatSession, Message, StreamEvent, TourDetailSection, User } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
export const MOCK_MODE = process.env.NEXT_PUBLIC_MOCK_MODE === "true";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const isUnauthorized = (e: unknown): boolean =>
  e instanceof ApiError ? e.status === 401 : (e as { status?: number })?.status === 401;

function url(path: string): string {
  return `${API_URL}${path}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url(path), {
      ...init,
      // httpOnly session cookie — must ride along cross-origin.
      credentials: "include",
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch (cause) {
    // A bare "Cannot reach the server" hides WHY, and the causes need
    // different fixes: the API being down, a browser extension blocking a
    // cross-origin request, or a CORS rejection. Log the real error and keep
    // it as `cause` so the console shows something actionable.
    const detail = cause instanceof Error ? cause.message : String(cause);
    console.error(`[api] ${init.method ?? "GET"} ${url(path)} failed:`, cause);
    throw new ApiError(
      `Cannot reach the server (${url(path) || "same origin"}): ${detail}`,
      0,
    );
  }

  if (!res.ok) throw new ApiError(await errorMessage(res), res.status);
  if (res.status === 204) return undefined as T;

  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError("Malformed JSON from server", res.status);
  }
}

/** Prefer the API's own {detail}/{message}/{error} over a bare status code. */
async function errorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as Record<string, unknown>;
    for (const k of ["detail", "message", "error"]) {
      const v = body[k];
      if (typeof v === "string" && v) return v;
    }
  } catch {
    /* not JSON */
  }
  if (res.status === 401) return "Invalid email or password";
  return `Request failed (${res.status})`;
}

export const api = {
  mockMode: MOCK_MODE,

  // --- auth ---
  register: (email: string, password: string): Promise<{ user: User }> =>
    MOCK_MODE
      ? mockApi.login(email, password)
      : request("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),

  login: (email: string, password: string): Promise<{ user: User }> =>
    MOCK_MODE
      ? mockApi.login(email, password)
      : request("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),

  logout: (): Promise<void> =>
    MOCK_MODE ? mockApi.logout() : request("/auth/logout", { method: "POST" }),

  me: (): Promise<{ user: User }> => (MOCK_MODE ? mockApi.me() : request("/auth/me")),

  // --- sessions ---
  listSessions: (): Promise<ChatSession[]> =>
    MOCK_MODE ? mockApi.listSessions() : request("/chat/sessions"),

  createSession: (title?: string): Promise<ChatSession> =>
    MOCK_MODE
      ? mockApi.createSession(title)
      : request("/chat/sessions", {
          method: "POST",
          body: JSON.stringify(title ? { title } : {}),
        }),

  renameSession: (id: string, title: string): Promise<ChatSession> =>
    MOCK_MODE
      ? mockApi.renameSession(id, title)
      : request(`/chat/sessions/${encodeURIComponent(id)}`, {
          method: "PATCH",
          body: JSON.stringify({ title }),
        }),

  deleteSession: (id: string): Promise<void> =>
    MOCK_MODE
      ? mockApi.deleteSession(id)
      : request(`/chat/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),

  listMessages: (id: string): Promise<Message[]> =>
    MOCK_MODE
      ? mockApi.listMessages(id)
      : request(`/chat/sessions/${encodeURIComponent(id)}/messages`),

  /**
   * Inclusions, exclusions and policies for one tour variant.
   *
   * Fetched only when the customer opens "Show details" -- one supplier call
   * per variant, so loading it for every row of a 17-tour search would be 17
   * calls for panels nobody opened.
   */
  tourOptionDetails: (
    tourId: number,
    optionId: string | number,
    supplierId: number,
  ): Promise<{ sections: TourDetailSection[] }> =>
    request(
      `/tours/${tourId}/options/${encodeURIComponent(String(optionId))}` +
        `/details?supplier_id=${supplierId}`,
    ),

  /**
   * Stream one assistant turn. Yields StreamEvents in arrival order; the
   * caller decides how to fold them into a message.
   */
  async *stream(
    sessionId: string,
    message: string,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamEvent> {
    if (MOCK_MODE) {
      yield* mockApi.stream(sessionId, message, signal);
      return;
    }

    let res: Response;
    try {
      res = await fetch(url("/chat/stream"), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ session_id: sessionId, message }),
        signal,
      });
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return;
      throw new ApiError("Cannot reach the server. Is the backend running?", 0);
    }

    if (!res.ok) throw new ApiError(await errorMessage(res), res.status);
    if (!res.body) throw new ApiError("Server sent no stream body", 500);

    yield* parseSSE(res.body, signal);
  },
};
