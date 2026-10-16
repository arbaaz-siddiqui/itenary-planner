import type { ChatSession } from "./types";

export interface SessionGroup {
  label: string;
  sessions: ChatSession[];
}

const DAY = 24 * 60 * 60 * 1000;

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/**
 * Pinned first, then Today / Yesterday / Previous 7 days / Older by last
 * activity. Empty groups are dropped, and order inside a group is newest first.
 */
export function groupSessions(
  sessions: ChatSession[],
  pinned: ReadonlySet<string>,
  now: Date = new Date(),
): SessionGroup[] {
  const today = startOfDay(now);
  const buckets: Record<string, ChatSession[]> = {
    Pinned: [],
    Today: [],
    Yesterday: [],
    "Previous 7 days": [],
    Older: [],
  };
  const sorted = [...sessions].sort(
    (a, b) => Date.parse(b.updated_at || b.created_at) - Date.parse(a.updated_at || a.created_at),
  );
  for (const s of sorted) {
    if (pinned.has(s.id)) {
      buckets.Pinned.push(s);
      continue;
    }
    const t = Date.parse(s.updated_at || s.created_at);
    if (Number.isNaN(t) || t >= today) buckets.Today.push(s);
    else if (t >= today - DAY) buckets.Yesterday.push(s);
    else if (t >= today - 7 * DAY) buckets["Previous 7 days"].push(s);
    else buckets.Older.push(s);
  }
  return Object.entries(buckets)
    .filter(([, list]) => list.length)
    .map(([label, list]) => ({ label, sessions: list }));
}
