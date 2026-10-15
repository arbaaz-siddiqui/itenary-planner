"use client";

import { useEffect, useState } from "react";
import { Monitor, Moon, Sun, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ThemePref } from "@/lib/theme";
import type { User } from "@/lib/types";

const THEMES: { id: ThemePref; label: string; icon: typeof Sun }[] = [
  { id: "dark", label: "Dark", icon: Moon },
  { id: "light", label: "Light", icon: Sun },
  { id: "system", label: "System", icon: Monitor },
];

const FIELD =
  "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-fg outline-none transition focus:border-brand-blue";

/** Settings: profile name and theme. Preferences and chat options follow. */
export function SettingsDialog({
  open,
  user,
  onUser,
  theme,
  onTheme,
  onClose,
}: {
  open: boolean;
  user: User | null;
  onUser: (u: User) => void;
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
  onClose: () => void;
}) {
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");

  useEffect(() => {
    if (!open) return;
    setFirst(user?.first_name ?? "");
    setLast(user?.last_name ?? "");
    setStatus("idle");
  }, [open, user]);

  if (!open) return null;

  const dirty = first.trim() !== (user?.first_name ?? "") || last.trim() !== (user?.last_name ?? "");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!first.trim()) return;
    setStatus("saving");
    try {
      const { user: u } = await api.updateProfile(first.trim(), last.trim());
      onUser(u);
      setStatus("saved");
    } catch {
      setStatus("error");
    }
  }

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
            className="grid h-8 w-8 place-items-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={18} aria-hidden />
          </button>
        </div>

        <form onSubmit={save} className="mb-6">
          <h3 className="mb-2 text-sm font-medium text-fg">Profile</h3>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-fg-muted">
              First name
              <input
                value={first}
                onChange={(e) => setFirst(e.target.value)}
                autoComplete="given-name"
                maxLength={80}
                required
                className={`${FIELD} mt-1`}
              />
            </label>
            <label className="text-xs text-fg-muted">
              Last name
              <input
                value={last}
                onChange={(e) => setLast(e.target.value)}
                autoComplete="family-name"
                maxLength={80}
                className={`${FIELD} mt-1`}
              />
            </label>
          </div>
          <p className="mt-2 text-xs text-fg-muted">{user?.email}</p>
          <div className="mt-3 flex items-center gap-3">
            <button
              type="submit"
              disabled={!dirty || !first.trim() || status === "saving"}
              className="rounded-lg bg-brand-blue px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-blue-dark disabled:opacity-50"
            >
              {status === "saving" ? "Saving…" : "Save"}
            </button>
            {status === "saved" && <span className="text-xs text-success">Saved</span>}
            {status === "error" && <span className="text-xs text-brand-red">Could not save. Try again.</span>}
          </div>
        </form>

        <section>
          <h3 className="mb-2 text-sm font-medium text-fg">Theme</h3>
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
