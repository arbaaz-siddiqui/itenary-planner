"use client";

import type { CardRef } from "./cardActions";

/**
 * Account data (saved cards, pinned chats, screen settings) is stored on the
 * server per account. Older builds kept it in this browser's localStorage,
 * first under one key shared by every account, then under per-account keys.
 *
 * `takeLocalAccountData` hands back what this browser still holds for THIS
 * account, so it can be uploaded once, and deletes every copy: this
 * account's, the shared ones, and any other account's. After it runs the
 * browser holds no account data at all.
 */
const SHARED_KEYS = ["saved-cards", "pinned-chats"];
const PER_ACCOUNT_PREFIXES = ["saved-cards:", "pinned-chats:"];

export function takeLocalAccountData(userId: string): { saved: CardRef[]; pinned: string[] } {
  const out: { saved: CardRef[]; pinned: string[] } = { saved: [], pinned: [] };
  try {
    const store = window.localStorage;
    const read = <T,>(key: string): T | null => {
      const raw = store.getItem(key);
      if (raw == null) return null;
      try {
        return JSON.parse(raw) as T;
      } catch {
        return null;
      }
    };
    const mine = read<CardRef[]>(`saved-cards:${userId}`);
    if (Array.isArray(mine)) out.saved = mine.filter((c) => c && typeof c.id === "string" && c.item);
    const pins = read<string[]>(`pinned-chats:${userId}`);
    if (Array.isArray(pins)) out.pinned = pins.filter((x) => typeof x === "string");

    const doomed: string[] = [...SHARED_KEYS];
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i);
      if (k && PER_ACCOUNT_PREFIXES.some((p) => k.startsWith(p))) doomed.push(k);
    }
    doomed.forEach((k) => store.removeItem(k));
    // Sidebar state moved to the account too.
    store.removeItem("sidebar-collapsed");
  } catch {
    /* storage blocked: nothing held, nothing to clear */
  }
  return out;
}
