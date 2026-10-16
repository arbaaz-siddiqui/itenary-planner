"use client";

import { forwardRef, useId, useState } from "react";
import { AlertCircle, Check, Eye, EyeOff, Loader2 } from "lucide-react";
import { TravelLoader } from "@/components/TravelLoader";

type FieldProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "id"> & {
  label: string;
  /** Shown under the field, in red, once the field has something to say. */
  error?: string | null;
  /** Quiet guidance under the field; `met` turns it green with a tick. */
  hint?: { text: string; met?: boolean } | null;
};

/**
 * Labelled input with its error under it. Password fields get a show/hide
 * toggle, since a typo in a hidden field is the commonest sign-up failure.
 */
export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field(
  { label, error, hint, type = "text", className, ...rest },
  ref,
) {
  const id = useId();
  const [shown, setShown] = useState(false);
  const isPassword = type === "password";
  const note = error ? `${id}-error` : hint ? `${id}-hint` : undefined;

  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1.5 block text-[13px] font-medium text-fg">
        {label}
      </label>
      <div className="relative">
        <input
          ref={ref}
          id={id}
          type={isPassword && shown ? "text" : type}
          aria-invalid={error ? true : undefined}
          aria-describedby={note}
          className={`h-11 w-full rounded-xl border bg-surface px-3.5 text-sm text-fg outline-none transition placeholder:text-fg-muted/60 focus:ring-4 ${
            isPassword ? "pr-11" : ""
          } ${
            error
              ? "border-brand-red/70 focus:border-brand-red focus:ring-brand-red/15"
              : "border-border hover:border-fg-muted/40 focus:border-accent focus:ring-accent/15"
          }`}
          {...rest}
        />
        {isPassword && (
          <button
            type="button"
            onClick={() => setShown((s) => !s)}
            aria-label={shown ? "Hide password" : "Show password"}
            aria-pressed={shown}
            className="press absolute top-1/2 right-1.5 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:ring-2 focus-visible:ring-accent/40 focus-visible:outline-none"
          >
            {shown ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        )}
      </div>
      {error ? (
        <p id={note} className="mt-1.5 flex items-center gap-1.5 text-xs text-brand-red">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {error}
        </p>
      ) : hint ? (
        <p
          id={note}
          className={`mt-1.5 flex items-center gap-1.5 text-xs transition-colors ${hint.met ? "text-success" : "text-fg-muted"}`}
        >
          {hint.met && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />}
          {hint.text}
        </p>
      ) : null}
    </div>
  );
});

export function SubmitButton({ busy, children, busyLabel }: { busy: boolean; children: React.ReactNode; busyLabel: string }) {
  return (
    <button
      type="submit"
      disabled={busy}
      aria-busy={busy}
      className="press flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-brand-blue text-sm font-semibold text-white shadow-[0_8px_24px_-12px_rgba(43,127,200,0.7)] hover:bg-brand-blue-dark focus-visible:ring-4 focus-visible:ring-accent/30 focus-visible:outline-none disabled:cursor-wait disabled:opacity-80"
    >
      {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {busy ? busyLabel : children}
    </button>
  );
}

/** A failure that belongs to the form, not one field (wrong password, taken email). */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className="status-in flex items-start gap-2 rounded-xl border border-brand-red/25 bg-brand-red/10 px-3.5 py-2.5 text-sm text-fg"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-brand-red" aria-hidden />
      {message}
    </p>
  );
}

/** Blank frame while the session check runs, so the form never flashes. */
export function AuthChecking() {
  return (
    <main className="grid min-h-dvh place-items-center bg-bg" aria-busy>
      <TravelLoader size="lg" label="Loading" />
    </main>
  );
}
