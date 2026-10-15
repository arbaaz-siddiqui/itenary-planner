// Server-safe: imported by the root layout, which is a server component.
// The theme hook itself lives in theme.ts ("use client").

export const THEME_KEY = "theme-pref";

/**
 * Runs inline in <head> before the first paint, so the page never flashes the
 * wrong palette. Kept dependency-free and tiny on purpose; storage can throw
 * (private mode, blocked site data), in which case dark wins.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{var p=localStorage.getItem("${THEME_KEY}")||"dark";var d=p==="system"?(matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"):p;document.documentElement.setAttribute("data-theme",d==="light"?"light":"dark");}catch(e){}})();`;
