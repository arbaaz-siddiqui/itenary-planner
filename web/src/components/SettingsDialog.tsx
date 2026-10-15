"use client";

import { useEffect, useState } from "react";
import { Download, Monitor, Moon, Sun, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import { Select } from "@/components/Select";
import type { ThemePref } from "@/lib/theme";
import type { Preferences, User } from "@/lib/types";

const THEMES: { id: ThemePref; label: string; icon: typeof Sun }[] = [
  { id: "dark", label: "Dark", icon: Moon },
  { id: "light", label: "Light", icon: Sun },
  { id: "system", label: "System", icon: Monitor },
];

const DIETS = [
  { value: "", label: "No preference" },
  { value: "Veg", label: "Vegetarian" },
  { value: "Jain", label: "Jain" },
  { value: "Vegan", label: "Vegan" },
];

const TABS = ["Profile", "Travel", "Appearance", "Chats"] as const;
type Tab = (typeof TABS)[number];

const FIELD =
  "w-full rounded-[10px] border border-border bg-bg px-3 py-2.5 text-sm text-fg outline-none transition focus:border-brand-blue focus:ring-2 focus:ring-brand-blue/20";
const LABEL = "mb-1.5 block text-sm font-medium text-fg";
const PRIMARY =
  "press rounded-[10px] bg-brand-blue px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-blue-dark disabled:opacity-50";

type Status = "idle" | "saving" | "saved" | "error";

function StatusNote({ status, error }: { status: Status; error?: string }) {
  if (status === "saved") return <span className="text-sm text-success">Saved</span>;
  if (status === "error") return <span className="text-sm text-brand-red">{error ?? "Not saved. Try again."}</span>;
  return null;
}

function ProfileTab({ user, onUser }: { user: User | null; onUser: (u: User) => void }) {
  const [first, setFirst] = useState(user?.first_name ?? "");
  const [last, setLast] = useState(user?.last_name ?? "");
  const [status, setStatus] = useState<Status>("idle");
  const dirty = first.trim() !== (user?.first_name ?? "") || last.trim() !== (user?.last_name ?? "");

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (!first.trim()) return;
        setStatus("saving");
        try {
          onUser((await api.updateProfile(first.trim(), last.trim())).user);
          setStatus("saved");
        } catch {
          setStatus("error");
        }
      }}
      className="space-y-4"
    >
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="st-first" className={LABEL}>
            First name
          </label>
          <input id="st-first" value={first} onChange={(e) => setFirst(e.target.value)} maxLength={80} required autoComplete="given-name" className={FIELD} />
        </div>
        <div>
          <label htmlFor="st-last" className={LABEL}>
            Last name
          </label>
          <input id="st-last" value={last} onChange={(e) => setLast(e.target.value)} maxLength={80} autoComplete="family-name" className={FIELD} />
        </div>
      </div>
      <div>
        <p className={LABEL}>Email</p>
        <p className="text-sm text-fg-muted">{user?.email}</p>
      </div>
      <div className="flex items-center gap-3">
        <button type="submit" disabled={!dirty || !first.trim() || status === "saving"} className={PRIMARY}>
          {status === "saving" ? "Saving" : "Save profile"}
        </button>
        <StatusNote status={status} />
      </div>
    </form>
  );
}

function TravelTab() {
  const [prefs, setPrefs] = useState<Preferences>({});
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState<Status>("idle");

  useEffect(() => {
    api
      .getPreferences()
      .then((r) => setPrefs(r.preferences ?? {}))
      .catch(() => setStatus("error"))
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded) {
    return (
      <div className="space-y-3" role="status" aria-label="Loading preferences">
        <div className="skeleton h-10 rounded-[10px]" />
        <div className="skeleton h-10 rounded-[10px]" />
        <div className="skeleton h-10 rounded-[10px]" />
      </div>
    );
  }

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setStatus("saving");
        try {
          const r = await api.setPreferences({
            origin_city: prefs.origin_city?.trim() ?? "",
            usual_party_size: prefs.usual_party_size || null,
            diet: prefs.diet ?? "",
            nationality: prefs.nationality?.trim() ?? "",
          });
          setPrefs(r.preferences ?? {});
          setStatus("saved");
        } catch {
          setStatus("error");
        }
      }}
      className="space-y-4"
    >
      <p className="text-sm text-fg-muted">The agent uses these at the start of every new chat, so it asks you less.</p>
      <div>
        <label htmlFor="st-city" className={LABEL}>
          Home city
        </label>
        <input
          id="st-city"
          value={prefs.origin_city ?? ""}
          onChange={(e) => setPrefs({ ...prefs, origin_city: e.target.value })}
          placeholder="Ahmedabad"
          maxLength={60}
          className={FIELD}
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="st-party" className={LABEL}>
            Usual travellers
          </label>
          <input
            id="st-party"
            type="number"
            min={1}
            max={20}
            value={prefs.usual_party_size ?? ""}
            onChange={(e) => setPrefs({ ...prefs, usual_party_size: e.target.value ? Number(e.target.value) : null })}
            className={FIELD}
          />
        </div>
        <div>
          <label htmlFor="st-diet" className={LABEL}>
            Food
          </label>
          <Select
            id="st-diet"
            value={prefs.diet ?? ""}
            options={DIETS}
            onChange={(v) => setPrefs({ ...prefs, diet: v as Preferences["diet"] })}
          />
        </div>
      </div>
      <div>
        <label htmlFor="st-nat" className={LABEL}>
          Passport country
        </label>
        <input
          id="st-nat"
          value={prefs.nationality ?? ""}
          onChange={(e) => setPrefs({ ...prefs, nationality: e.target.value })}
          placeholder="India"
          maxLength={60}
          className={FIELD}
        />
      </div>
      <p className="text-xs text-fg-muted">Prices are shown in Indian rupees (INR).</p>
      <div className="flex items-center gap-3">
        <button type="submit" disabled={status === "saving"} className={PRIMARY}>
          {status === "saving" ? "Saving" : "Save preferences"}
        </button>
        <StatusNote status={status} />
      </div>
    </form>
  );
}

function AppearanceTab({ theme, onTheme }: { theme: ThemePref; onTheme: (t: ThemePref) => void }) {
  return (
    <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-2">
      {THEMES.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={theme === id}
          onClick={() => onTheme(id)}
          className={`press flex flex-col items-center gap-2 rounded-xl border px-3 py-4 text-sm ${
            theme === id ? "border-brand-blue bg-brand-blue/10 text-fg" : "border-border text-fg-muted hover:border-fg-muted hover:text-fg"
          }`}
        >
          <Icon size={20} aria-hidden />
          {label}
        </button>
      ))}
    </div>
  );
}

function ChatsTab({ onExport, onClearAll, canExport }: { onExport: () => void; onClearAll: () => Promise<void>; canExport: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  return (
    <div className="space-y-5">
      <div>
        <p className={LABEL}>Export this chat</p>
        <p className="mb-2 text-sm text-fg-muted">Download the open chat as a text file.</p>
        <button
          type="button"
          onClick={onExport}
          disabled={!canExport}
          className="press inline-flex items-center gap-2 rounded-[10px] border border-border px-4 py-2.5 text-sm font-medium text-fg hover:bg-surface-2 disabled:opacity-50"
        >
          <Download size={16} aria-hidden />
          Export chat
        </button>
      </div>
      <div>
        <p className={LABEL}>Delete all chats</p>
        <p className="mb-2 text-sm text-fg-muted">Removes every chat from your account. This cannot be undone.</p>
        {confirming ? (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={async () => {
                setStatus("saving");
                try {
                  await onClearAll();
                  setStatus("saved");
                  setConfirming(false);
                } catch {
                  setStatus("error");
                }
              }}
              className="press rounded-[10px] bg-brand-red px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50"
              disabled={status === "saving"}
            >
              {status === "saving" ? "Deleting" : "Delete all chats"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} className="press rounded-[10px] px-4 py-2.5 text-sm text-fg-muted hover:text-fg">
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="press inline-flex items-center gap-2 rounded-[10px] border border-brand-red/40 px-4 py-2.5 text-sm font-medium text-brand-red hover:bg-brand-red/10"
          >
            <Trash2 size={16} aria-hidden />
            Delete all chats
          </button>
        )}
        {status === "saved" && <p className="mt-2 text-sm text-success">All chats deleted.</p>}
        {status === "error" && <p className="mt-2 text-sm text-brand-red">Some chats were not deleted. Try again.</p>}
      </div>
    </div>
  );
}

/** Settings: profile, travel preferences, appearance and chat history. */
export function SettingsDialog({
  open,
  user,
  onUser,
  theme,
  onTheme,
  onExport,
  onClearAll,
  canExport,
  onClose,
}: {
  open: boolean;
  user: User | null;
  onUser: (u: User) => void;
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
  onExport: () => void;
  onClearAll: () => Promise<void>;
  canExport: boolean;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("Profile");
  useEffect(() => {
    if (open) setTab("Profile");
  }, [open]);
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 grid items-end justify-items-center bg-black/55 backdrop-blur-[2px] sm:place-items-center sm:px-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
        className="panel-pop flex max-h-[92dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl border border-border bg-surface pb-[env(safe-area-inset-bottom)] shadow-2xl sm:max-h-[88dvh] sm:rounded-2xl sm:pb-0"
      >
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <h2 id="settings-title" className="text-lg font-semibold tracking-tight">
            Settings
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            className="press grid h-9 w-9 place-items-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={18} aria-hidden />
          </button>
        </div>
        <div role="tablist" aria-label="Settings sections" className="no-scrollbar flex gap-1 overflow-x-auto border-b border-border px-3 sm:px-5">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`-mb-px flex-1 border-b-2 px-2 py-2.5 text-sm font-medium whitespace-nowrap transition-colors sm:flex-none sm:px-3 ${
                tab === t ? "border-brand-blue text-fg" : "border-transparent text-fg-muted hover:text-fg"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="min-h-0 overflow-y-auto px-6 py-5" role="tabpanel">
          {tab === "Profile" && <ProfileTab user={user} onUser={onUser} />}
          {tab === "Travel" && <TravelTab />}
          {tab === "Appearance" && <AppearanceTab theme={theme} onTheme={onTheme} />}
          {tab === "Chats" && <ChatsTab onExport={onExport} onClearAll={onClearAll} canExport={canExport} />}
        </div>
      </div>
    </div>
  );
}
