// The planned trip as the itinerary card shows it: each day time by time,
// every paid part carrying its own price from the plan's cost lines, and the
// total. Pure, so it is tested; components/ItineraryView.tsx draws it.

import type { OptionItem } from "./types";

export type StepKind = "flight" | "transfer" | "hotel" | "tour" | "meal" | "free" | "other";

export interface Step {
  start: string;
  end: string;
  title: string;
  detail: string;
  kind: StepKind;
  /** The plan's price for this part, when it is a paid part of the trip. */
  price: number | null;
  /** A meal served inside a tour, or a transfer the package includes. */
  included: boolean;
}

export interface Day {
  number: number;
  date: string;
  label: string;
  steps: Step[];
}

export interface CostLine {
  item: string;
  detail: string;
  amount: number;
}

export interface Itinerary {
  startDate: string;
  endDate: string;
  nights: number;
  adults: number;
  days: Day[];
  lines: CostLine[];
  total: number | null;
  /** Something the total does not cover yet, in plain words. */
  notes: string[];
}

const KINDS: StepKind[] = ["flight", "transfer", "hotel", "tour", "meal", "free"];

const str = (o: unknown, k: string): string => {
  const v = (o as Record<string, unknown> | null)?.[k];
  return typeof v === "string" ? v : "";
};
const num = (o: unknown, k: string): number | null => {
  const v = (o as Record<string, unknown> | null)?.[k];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
};

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** Read the itinerary card's payload; null when it is not a plan. */
export function toItinerary(raw: OptionItem | null | undefined): Itinerary | null {
  const days = Array.isArray(raw?.days) ? (raw!.days as unknown[]) : [];
  if (!days.length) return null;
  const lines: CostLine[] = (Array.isArray(raw?.package_lines) ? (raw!.package_lines as unknown[]) : [])
    .map((l) => ({ item: str(l, "item"), detail: str(l, "detail"), amount: num(l, "amount_inr") ?? 0, date: str(l, "date") }))
    .filter((l) => l.item && l.amount > 0)
    .map(({ item, detail, amount }) => ({ item, detail, amount }));
  const rawLines = (Array.isArray(raw?.package_lines) ? (raw!.package_lines as unknown[]) : []).map((l) => ({
    item: norm(str(l, "item")),
    date: str(l, "date"),
    amount: num(l, "amount_inr"),
  }));

  // A line belongs to a step when they name the same thing on the same day;
  // the airport legs are named by direction ("Airport pickup to the hotel").
  const priceFor = (title: string, date: string, kind: StepKind): number | null => {
    const t = norm(title);
    if (kind === "tour") {
      return rawLines.find((l) => l.item === t && l.date === date)?.amount ?? rawLines.find((l) => l.item === t)?.amount ?? null;
    }
    if (kind === "transfer") {
      const leg = t.startsWith("airport pickup") ? "airport pickup" : t.startsWith("airport drop off") ? "airport drop off" : "";
      return leg ? (rawLines.find((l) => l.item === leg)?.amount ?? null) : null;
    }
    return null;
  };

  const parsed: Day[] = days.map((d, i) => {
    const date = str(d, "date");
    const items = Array.isArray((d as Record<string, unknown>)?.items) ? ((d as Record<string, unknown>).items as unknown[]) : [];
    return {
      number: num(d, "day_number") ?? i + 1,
      date,
      label: str(d, "label"),
      steps: items.map((it) => {
        const k = str(it, "kind") as StepKind;
        const kind: StepKind = KINDS.includes(k) ? k : "other";
        const title = str(it, "title");
        return {
          start: str(it, "start"),
          end: str(it, "end"),
          title,
          detail: str(it, "detail"),
          kind,
          price: priceFor(title, date, kind),
          included: (it as Record<string, unknown>)?.included === true,
        };
      }),
    };
  });

  const startDate = str(raw, "start_date") || parsed[0]?.date || "";
  return {
    startDate,
    endDate: parsed[parsed.length - 1]?.date ?? startDate,
    nights: Math.max(0, parsed.length - 1),
    adults: num(raw, "adults") ?? 0,
    days: parsed,
    lines,
    total: num(raw, "total_inr"),
    notes: (Array.isArray(raw?.notes) ? (raw!.notes as unknown[]) : [])
      .filter((n): n is string => typeof n === "string" && n.trim() !== "")
      // Written for the agent; the card says it to the customer.
      .map((n) => (/visa/i.test(n) ? "The UAE visa is needed; its price will be added to the total." : n)),
  };
}

/** "Fri, 6 Nov" for a yyyy-mm-dd date. */
export function dayHeading(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}
