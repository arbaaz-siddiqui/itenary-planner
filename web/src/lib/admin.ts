// Admin panel: response shapes and the pure logic its screens use.
// Everything here is plain TypeScript so Vitest covers it (no DOM).

import type { OptionGroup } from "./types";

// ------------------------------------------------------------------ shapes

export interface AdminKpis {
  total_users: number;
  new_users: number;
  active_users: number;
  chats: number;
  turns: number;
  searches: number;
  failed_searches: number;
  thumbs_up: number;
  thumbs_down: number;
}

export interface AdminDay {
  day: string;
  active_users: number;
  signups: number;
  turns: number;
  searches: number;
  failed_searches: number;
}

export interface AdminSearch {
  id: string;
  user_id: string;
  email: string | null;
  session_id: string | null;
  tool: string;
  origin?: string | null;
  destination: string | null;
  start_date: string | null;
  party?: string | null;
  result_count?: number | null;
  error: string | null;
  latency_ms?: number | null;
  args?: Record<string, unknown>;
  created_at: string;
}

export interface AdminOverview {
  days: number;
  kpis: AdminKpis;
  daily: AdminDay[];
  searches_by_tool: { tool: string; searches: number; failed: number }[];
  top_destinations: { destination: string; searches: number }[];
  recent_failed: AdminSearch[];
}

export interface AdminUserRow {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  created_at: string;
  last_active: string | null;
  chats: number;
  messages: number;
  searches: number;
}

export interface AdminUserList {
  total: number;
  page: number;
  page_size: number;
  users: AdminUserRow[];
}

export interface AdminSessionRow {
  id: string;
  title: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  messages: number;
  searches: number;
}

export interface AdminUserDetail {
  user: { id: string; email: string; first_name: string; last_name: string; created_at: string };
  facts: Record<string, unknown>;
  saved_cards: number;
  searches: number;
  last_search: string | null;
  last_active: string | null;
  sessions: AdminSessionRow[];
}

export interface AdminToolCall {
  tool_name: string;
  input: unknown;
  output_preview: string;
  output_chars?: number;
}

export interface AdminMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  options: OptionGroup[];
  feedback: "up" | "down" | null;
  tool_calls: AdminToolCall[];
}

export interface AdminTranscript {
  session: { id: string; title: string; created_at: string; updated_at: string };
  user: { id: string; email: string; first_name: string; last_name: string };
  messages: AdminMessage[];
}

export interface AdminSearchList {
  total: number;
  page: number;
  page_size: number;
  searches: AdminSearch[];
  tools: string[];
}

export interface SearchFilters {
  tool?: string;
  user_id?: string;
  q?: string;
  errors_only?: boolean;
  from?: string;
  to?: string;
  page?: number;
}

// ---------------------------------------------------------------- services

export type AdminService =
  | "Flights"
  | "Hotels"
  | "Tours"
  | "Transfers"
  | "Restaurants"
  | "Visa"
  | "Packages"
  | "Planning"
  | "Other";

/** Which customer-facing service a tool belongs to. */
export function serviceOfTool(tool: string): AdminService {
  const t = tool.toLowerCase();
  if (t.includes("flight")) return "Flights";
  if (t.includes("hotel")) return "Hotels";
  if (t.includes("tour")) return "Tours";
  if (t.includes("transfer")) return "Transfers";
  if (t.includes("restaurant")) return "Restaurants";
  if (t.includes("visa")) return "Visa";
  if (t.includes("package")) return "Packages";
  if (/(plan_itinerary|itinerary_pdf|trip_schedule|trip_total|hotel_block)/.test(t)) return "Planning";
  return "Other";
}

/** Searches per service, biggest first; "Other" always last. */
export function groupByService(
  rows: { tool: string; searches: number; failed: number }[],
): { service: AdminService; searches: number; failed: number }[] {
  const acc = new Map<AdminService, { searches: number; failed: number }>();
  for (const r of rows) {
    const s = serviceOfTool(r.tool);
    const cur = acc.get(s) ?? { searches: 0, failed: 0 };
    acc.set(s, { searches: cur.searches + r.searches, failed: cur.failed + r.failed });
  }
  return [...acc.entries()]
    .map(([service, v]) => ({ service, ...v }))
    .sort((a, b) =>
      a.service === "Other" ? 1 : b.service === "Other" ? -1 : b.searches - a.searches || a.service.localeCompare(b.service),
    );
}

const TOOL_LABELS: Record<string, string> = {
  search_flights: "Flight search",
  get_flight_details: "Flight details",
  search_hotels: "Hotel search",
  get_hotel_info: "Hotel info",
  get_hotel_description: "Hotel description",
  get_hotel_reviews: "Hotel reviews",
  list_city_hotels: "City hotels",
  lookup_hotel_city: "Hotel city lookup",
  search_tours: "Tour search",
  get_tour_details: "Tour details",
  get_tour_options: "Tour options",
  get_tour_timeslots: "Tour time slots",
  search_airport_transfer_dubai: "Airport transfer search",
  search_airport_transfer: "Airport transfer search",
  get_transfer_details: "Transfer details",
  search_restaurants: "Restaurant search",
  get_restaurant_details: "Restaurant details",
  get_visa_info: "Visa lookup",
  list_visa_countries: "Visa countries",
  list_packages: "Package list",
  get_package_details: "Package details",
  plan_itinerary_tool: "Trip plan",
  generate_itinerary_pdf_tool: "Itinerary PDF",
  lookup_entity: "Place lookup",
  get_exchange_rate: "Exchange rate",
};

/** "search_flights" -> "Flight search"; unknown tools are made readable. */
export function toolLabel(tool: string): string {
  if (TOOL_LABELS[tool]) return TOOL_LABELS[tool];
  const words = tool.replace(/_tool$/, "").replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Unknown";
}

// ------------------------------------------------------------------ charts

/** Bar heights as percentages of the largest value (0 when all are zero). */
export function barPercents(values: number[]): number[] {
  const max = Math.max(0, ...values);
  return values.map((v) => (max > 0 ? Math.round((Math.max(0, v) / max) * 1000) / 10 : 0));
}

/**
 * An SVG path through the values, scaled into a w x h box (y grows down).
 * One value draws a flat line across; none gives an empty path.
 */
export function sparkPath(values: number[], w: number, h: number, pad = 2): string {
  if (!values.length) return "";
  const max = Math.max(...values);
  const min = Math.min(0, ...values);
  const span = max - min || 1;
  const pts = values.length === 1 ? [values[0], values[0]] : values;
  const step = (w - pad * 2) / (pts.length - 1);
  return pts
    .map((v, i) => {
      const x = pad + i * step;
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      return `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

/** Which x-axis labels to print so they never crowd: first, last and evenly between. */
export function axisTicks(count: number, maxTicks = 6): number[] {
  if (count <= 0) return [];
  if (count <= maxTicks) return Array.from({ length: count }, (_, i) => i);
  const out = new Set<number>([0, count - 1]);
  const step = (count - 1) / (maxTicks - 1);
  for (let i = 1; i < maxTicks - 1; i++) out.add(Math.round(i * step));
  return [...out].sort((a, b) => a - b);
}

/** "12 Nov" from "2026-11-12" (a calendar day, no time zone shift). */
export function shortDay(day: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  if (!m) return day;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${Number(m[3])} ${months[Number(m[2]) - 1] ?? ""}`.trim();
}

/** Share of failures as "4.2%" (no searches gives "0%"). */
export function failureRate(searches: number, failed: number): string {
  if (!searches) return "0%";
  const pct = (failed / searches) * 100;
  return `${pct >= 10 || Number.isInteger(pct) ? Math.round(pct) : pct.toFixed(1)}%`;
}

/** Thumbs up as a share of all ratings, or null when nothing was rated. */
export function satisfaction(up: number, down: number): string | null {
  const n = up + down;
  return n ? `${Math.round((up / n) * 100)}%` : null;
}

/** Whole numbers in en-IN grouping: 12,34,567. */
export function formatCount(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

// ------------------------------------------------------------------ people

export function fullNameOf(u: { first_name?: string; last_name?: string; email?: string | null }): string {
  const name = [u.first_name, u.last_name].filter((s) => s && s.trim()).join(" ").trim();
  return name || (u.email ?? "").split("@")[0] || "Unnamed";
}

/** "Just now", "5 min ago", "3 h ago", "Yesterday", "4 days ago", else "12 Nov 2026". */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "Never";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "Never";
  const secs = Math.round((now.getTime() - t.getTime()) / 1000);
  if (secs < 60) return "Just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  return dateLabel(iso);
}

/** "12 Nov 2026" in India time. */
export function dateLabel(iso: string): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  return t.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}

/** "12 Nov 2026, 3:04 pm" in India time. */
export function dateTimeLabel(iso: string): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  return t.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  });
}

const FACT_LABELS: Record<string, string> = {
  origin_city: "Home city",
  usual_party_size: "Usual party size",
  diet: "Diet",
  nationality: "Nationality",
};

/** Remembered facts as label / value pairs, known ones first. */
export function factRows(facts: Record<string, unknown>): { label: string; value: string }[] {
  const keys = Object.keys(facts).filter((k) => facts[k] !== null && facts[k] !== "" && facts[k] !== undefined);
  keys.sort((a, b) => {
    const ia = Object.keys(FACT_LABELS).indexOf(a);
    const ib = Object.keys(FACT_LABELS).indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  return keys.map((k) => {
    const v = facts[k];
    const label = FACT_LABELS[k] ?? toolLabel(k);
    return { label, value: typeof v === "object" ? JSON.stringify(v) : String(v) };
  });
}

// ------------------------------------------------------------------ paging

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, pageSize)));
}

/** "1-25 of 130" for the pager. */
export function rangeLabel(page: number, pageSize: number, total: number): string {
  if (!total) return "0 results";
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(total, page * pageSize);
  return `${formatCount(start)}-${formatCount(end)} of ${formatCount(total)}`;
}

/** Query string for /admin-api/searches; empty filters are left out. */
export function searchQuery(f: SearchFilters): string {
  const p = new URLSearchParams();
  if (f.tool) p.set("tool", f.tool);
  if (f.user_id) p.set("user_id", f.user_id);
  if (f.q && f.q.trim()) p.set("q", f.q.trim());
  if (f.errors_only) p.set("errors_only", "true");
  if (f.from) p.set("from", f.from);
  if (f.to) p.set("to", f.to);
  if (f.page && f.page > 1) p.set("page", String(f.page));
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** One line describing what was searched: "Mumbai to Dubai, 10 Dec 2026, 2 adults". */
export function searchSummary(
  s: Pick<AdminSearch, "origin" | "destination" | "start_date" | "party"> & { args?: Record<string, unknown> },
): string {
  // A transfer names a hotel, not a city: shown, but never counted as a destination.
  const hotel = typeof s.args?.hotel_name === "string" ? s.args.hotel_name.trim() : "";
  const where =
    s.origin && s.destination ? `${s.origin} to ${s.destination}` : s.destination || s.origin || (hotel ? `To ${hotel}` : "");
  return [where, s.start_date || "", s.party || ""].filter(Boolean).join(", ") || "No details";
}

/** Pretty JSON for the tool call panel; text that is not JSON is shown as is. */
export function prettyJson(value: unknown, max = 4000): string {
  let text: string;
  if (typeof value === "string") {
    try {
      text = JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      text = value;
    }
  } else {
    text = JSON.stringify(value ?? null, null, 2) ?? "";
  }
  return text.length > max ? `${text.slice(0, max)}\n…` : text;
}
