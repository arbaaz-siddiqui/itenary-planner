"use client";

import { useCallback, useEffect, useState } from "react";
import { THEME_KEY } from "./themeBoot";

/** What the user chose. "system" follows the OS. */
export type ThemePref = "dark" | "light" | "system";

function resolve(pref: ThemePref): "dark" | "light" {
  if (pref !== "system") return pref;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function readPref(): ThemePref {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return v === "light" || v === "system" ? v : "dark";
  } catch {
    return "dark";
  }
}

/** Current theme preference, and a setter that applies and remembers it. */
export function useTheme() {
  const [pref, setPrefState] = useState<ThemePref>("dark");

  useEffect(() => {
    setPrefState(readPref());
  }, []);

  // Follow the OS while "system" is selected.
  useEffect(() => {
    if (pref !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const sync = () => document.documentElement.setAttribute("data-theme", resolve("system"));
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [pref]);

  const setPref = useCallback((next: ThemePref) => {
    setPrefState(next);
    document.documentElement.setAttribute("data-theme", resolve(next));
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
  }, []);

  return { pref, setPref, resolved: typeof window === "undefined" ? "dark" : resolve(pref) };
}
