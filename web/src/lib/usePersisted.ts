"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * useState that survives a reload, for per-browser UI conveniences only
 * (sidebar collapsed, pinned chats). Storage can be blocked or empty, so every
 * read and write is guarded and the default always works.
 */
export function usePersisted<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(initial);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw != null) setValue(JSON.parse(raw) as T);
    } catch {
      /* unreadable or blocked: keep the default */
    }
  }, [key]);

  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const v = typeof next === "function" ? (next as (p: T) => T)(prev) : next;
        try {
          window.localStorage.setItem(key, JSON.stringify(v));
        } catch {
          /* blocked: lasts for this page only */
        }
        return v;
      });
    },
    [key],
  );

  return [value, set] as const;
}
