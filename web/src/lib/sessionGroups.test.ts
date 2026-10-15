import { describe, expect, it } from "vitest";
import { groupSessions } from "./sessionGroups";

const at = (id: string, iso: string) => ({ id, title: id, created_at: iso, updated_at: iso });
const NOW = new Date(2026, 9, 15, 12, 0, 0);

describe("groupSessions", () => {
  it("buckets by last activity, newest first, and drops empty groups", () => {
    const groups = groupSessions(
      [
        at("old", new Date(2026, 8, 1).toISOString()),
        at("today-early", new Date(2026, 9, 15, 8).toISOString()),
        at("today-late", new Date(2026, 9, 15, 11).toISOString()),
        at("yesterday", new Date(2026, 9, 14, 20).toISOString()),
        at("week", new Date(2026, 9, 10).toISOString()),
      ],
      new Set(),
      NOW,
    );
    expect(groups.map((g) => g.label)).toEqual(["Today", "Yesterday", "Previous 7 days", "Older"]);
    expect(groups[0].sessions.map((s) => s.id)).toEqual(["today-late", "today-early"]);
  });

  it("puts pinned chats first, whatever their age", () => {
    const groups = groupSessions(
      [at("a", new Date(2026, 9, 15, 9).toISOString()), at("b", new Date(2026, 5, 1).toISOString())],
      new Set(["b"]),
      NOW,
    );
    expect(groups[0]).toEqual({ label: "Pinned", sessions: [expect.objectContaining({ id: "b" })] });
  });
});
