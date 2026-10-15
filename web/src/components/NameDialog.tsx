"use client";

import { useEffect, useRef, useState } from "react";
import { api, isUnauthorized } from "@/lib/api";
import type { User } from "@/lib/types";

const FIELD =
  "w-full rounded-[10px] border border-border bg-bg px-3 py-2.5 text-sm text-fg outline-none transition placeholder:text-fg-muted focus:border-brand-blue focus:ring-2 focus:ring-brand-blue/20";

/**
 * Asked once of accounts created before sign-up collected a name. New
 * accounts give it at sign-up and never see this. "Not now" hides it for
 * this visit only; the greeting falls back to the email until it is filled.
 */
export function NameDialog({
  open,
  onSaved,
  onLater,
  onSignedOut,
}: {
  open: boolean;
  onSaved: (u: User) => void;
  onLater: () => void;
  onSignedOut: () => void;
}) {
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const firstRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) requestAnimationFrame(() => firstRef.current?.focus());
  }, [open]);

  if (!open) return null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const f = first.trim();
    if (!f) {
      setError("Enter your first name.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const { user } = await api.updateProfile(f, last.trim());
      onSaved(user);
    } catch (err) {
      if (isUnauthorized(err)) {
        onSignedOut();
        return;
      }
      setError(err instanceof Error && err.message ? err.message : "Your name was not saved. Try again.");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/55 px-4 backdrop-blur-[2px]">
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="name-title"
        onSubmit={save}
        onKeyDown={(e) => e.key === "Escape" && onLater()}
        className="panel-pop w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-2xl"
      >
        <h2 id="name-title" className="text-lg font-semibold tracking-tight text-fg">
          What should we call you?
        </h2>
        <p className="mt-1 text-sm text-fg-muted">We use your first name to greet you.</p>

        <div className="mt-5 space-y-3">
          <div>
            <label htmlFor="nd-first" className="mb-1.5 block text-sm font-medium text-fg">
              First name
            </label>
            <input
              ref={firstRef}
              id="nd-first"
              value={first}
              onChange={(e) => setFirst(e.target.value)}
              autoComplete="given-name"
              maxLength={80}
              required
              className={FIELD}
            />
          </div>
          <div>
            <label htmlFor="nd-last" className="mb-1.5 block text-sm font-medium text-fg">
              Last name
            </label>
            <input
              id="nd-last"
              value={last}
              onChange={(e) => setLast(e.target.value)}
              autoComplete="family-name"
              maxLength={80}
              className={FIELD}
            />
          </div>
        </div>

        {error && (
          <p role="alert" className="mt-3 text-sm text-brand-red">
            {error}
          </p>
        )}

        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onLater}
            className="press rounded-[10px] px-4 py-2.5 text-sm font-medium text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            Not now
          </button>
          <button
            type="submit"
            disabled={saving || !first.trim()}
            className="press rounded-[10px] bg-brand-blue px-5 py-2.5 text-sm font-medium text-white hover:bg-brand-blue-dark disabled:opacity-50"
          >
            {saving ? "Saving" : "Save name"}
          </button>
        </div>
      </form>
    </div>
  );
}
