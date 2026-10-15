"use client";

import { Monitor, Moon, Sun, X } from "lucide-react";
import type { ThemePref } from "@/lib/theme";

const THEMES: { id: ThemePref; label: string; icon: typeof Sun }[] = [
  { id: "dark", label: "Dark", icon: Moon },
  { id: "light", label: "Light", icon: Sun },
  { id: "system", label: "System", icon: Monitor },
];

/** Settings. Theme now; profile, preferences and chat options follow. */
export function SettingsDialog({
  open,
  email,
  theme,
  onTheme,
  onClose,
}: {
  open: boolean;
  email: string;
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 px-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
        className="w-full max-w-md rounded-2xl border border-border bg-surface p-5 shadow-2xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id="settings-title" className="text-lg font-semibold">
            Settings
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            autoFocus
            className="grid h-8 w-8 place-items-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={18} aria-hidden />
          </button>
        </div>

        <section className="mb-5">
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">Account</h3>
          <p className="text-sm">{email}</p>
        </section>

        <section>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">Theme</h3>
          <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-2">
            {THEMES.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={theme === id}
                onClick={() => onTheme(id)}
                className={`flex flex-col items-center gap-1.5 rounded-xl border px-3 py-3 text-sm transition ${
                  theme === id
                    ? "border-brand-blue bg-brand-blue/10 text-fg"
                    : "border-border text-fg-muted hover:border-fg-muted hover:text-fg"
                }`}
              >
                <Icon size={18} aria-hidden />
                {label}
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
