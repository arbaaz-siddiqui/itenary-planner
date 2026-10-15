"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * useState that survives a reload, for UI conveniences only (sidebar
 * collapsed, pinned chats, saved cards). Storage can be blocked or empty, so
 * every read and write is guarded and the default always works.
 *
 * Anything that belongs to an account must use a per-account key (see
 * `userKey`): the browser is shared by every account that signs in on it.
 * A `null` key holds the default and stores nothing, for the moment before
 * the account is known. Switching keys never carries the previous key's
 * value over: a key with nothing stored starts from the default.
 */
export function usePersisted<T>(key: string | null, initial: T) {
  const [value, setValue] = useState<T>(initial);
  const initialRef = useRef(initial);
  const keyRef = useRef(key);
  keyRef.current = key;

  useEffect(() => {
    let next = initialRef.current;
    if (key) {
      try {
        const raw = window.localStorage.getItem(key);
        if (raw != null) next = JSON.parse(raw) as T;
      } catch {
        /* unreadable or blocked: keep the default */
      }
    }
    setValue(next);
  }, [key]);

  const set = useCallback((next: T | ((prev: T) => T)) => {
    setValue((prev) => {
      const v = typeof next === "function" ? (next as (p: T) => T)(prev) : next;
      const k = keyRef.current;
      if (k) {
        try {
          window.localStorage.setItem(k, JSON.stringify(v));
        } catch {
          /* blocked: lasts for this page only */
        }
      }
      return v;
    });
  }, []);

  return [value, set] as const;
}

/** A storage key scoped to one account, or null until the account is known. */
export function userKey(name: string, userId: string | null | undefined): string | null {
  return userId ? `${name}:${userId}` : null;
}

/**
 * Keys written before storage was scoped per account. Whoever's data they
 * hold, it must not show to the next account on this browser.
 */
export const LEGACY_SHARED_KEYS = ["saved-cards", "pinned-chats"];
