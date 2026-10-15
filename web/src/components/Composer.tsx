"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { ArrowUp, MessageCircle, Route, Square, X } from "lucide-react";
import { serviceById, type ServiceId } from "@/lib/services";

/** About six lines; then the box scrolls inside itself. */
const MAX_HEIGHT_PX = 168;

export type ComposerMode = "ask" | "plan";

export interface ComposerHandle {
  /** Drop text into the box (a starter prompt, "Ask the agent about this"). */
  prefill: (text: string) => void;
  focus: () => void;
}

const MODES = [
  { id: "ask", label: "Ask", icon: MessageCircle, hint: "Quick answers: prices, options, policies" },
  { id: "plan", label: "Plan", icon: Route, hint: "Full trip: flights, hotel and tours, day by day" },
] as const;

export const Composer = forwardRef<
  ComposerHandle,
  {
    onSend: (text: string) => void;
    onStop: () => void;
    streaming: boolean;
    disabled?: boolean;
    /** "hero" is the big centred box on the home screen; "dock" sits under the chat. */
    variant?: "hero" | "dock";
    service?: ServiceId | null;
    onClearService?: () => void;
    mode: ComposerMode;
    onModeChange: (m: ComposerMode) => void;
  }
>(function Composer(
  { onSend, onStop, streaming, disabled, variant = "dock", service, onClearService, mode, onModeChange },
  ref,
) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  const svc = serviceById(service);

  useImperativeHandle(ref, () => ({
    prefill: (t: string) => {
      setText(t);
      requestAnimationFrame(() => {
        const el = area.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(t.length, t.length);
      });
    },
    focus: () => area.current?.focus(),
  }));

  // Grow with content up to a cap, then scroll internally.
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  function submit() {
    const t = text.trim();
    if (!t || streaming || disabled) return;
    setText("");
    onSend(t);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter newlines. Skip while an IME is composing.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  const hero = variant === "hero";

  const box = (
    <div
      className={`w-full rounded-3xl border border-border bg-surface shadow-lg transition focus-within:border-brand-blue/60 ${
        hero ? "shadow-black/30" : "shadow-black/10"
      }`}
    >
      {/* Text and mode toggle on the left; send, centred on the right. */}
      <div className="flex items-center gap-2 pr-3">
      <div className="min-w-0 flex-1">
      {svc && (
        <div className="px-4 pt-3">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-blue/15 py-1 pl-2.5 pr-1 text-xs font-medium text-link">
            <svc.icon size={13} aria-hidden />
            {svc.label}
            <button
              type="button"
              onClick={onClearService}
              aria-label={`Remove ${svc.label}`}
              className="grid h-5 w-5 place-items-center rounded-full hover:bg-brand-blue/20"
            >
              <X size={12} aria-hidden />
            </button>
          </span>
        </div>
      )}
      <textarea
        ref={area}
        rows={hero ? 2 : 1}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={
          mode === "plan"
            ? "Describe the trip: where, when, who is travelling…"
            : svc
              ? `Ask about ${svc.label.toLowerCase()}…`
              : "Ask about flights, hotels, tours, visas…"
        }
        aria-label="Message"
        className={`block w-full resize-none bg-transparent px-4 text-fg outline-none placeholder:text-fg-muted disabled:opacity-50 ${
          hero ? "pt-4 text-base" : "pt-3 text-[15px]"
        }`}
      />
      <div className="flex items-center gap-2 px-2 pb-2 pl-3 pt-1">
        {/* Ask answers the question in hand; Plan builds the whole trip. The
            server turns Plan into an instruction to gather the essentials and
            produce a priced day-by-day itinerary. */}
        <div role="radiogroup" aria-label="Mode" className="flex rounded-full bg-surface-2 p-0.5 text-xs">
          {MODES.map(({ id, label, icon: Icon, hint }) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={mode === id}
              title={hint}
              onClick={() => onModeChange(id)}
              className={`press inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 font-medium ${
                mode === id ? "bg-brand-blue text-white shadow-sm" : "text-fg-muted hover:text-fg"
              }`}
            >
              <Icon size={13} aria-hidden />
              {label}
            </button>
          ))}
        </div>
        <p className="hidden min-w-0 flex-1 truncate text-xs text-fg-muted sm:block">
          {MODES.find((m) => m.id === mode)?.hint}
        </p>
        <div className="flex-1 sm:hidden" />
      </div>
      </div>
          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop generating"
              className="press grid h-11 w-11 shrink-0 place-items-center rounded-full bg-fg text-bg hover:opacity-85"
            >
              <Square size={13} fill="currentColor" aria-hidden />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!text.trim() || disabled}
              aria-label="Send message"
              className="press grid h-11 w-11 shrink-0 place-items-center rounded-full bg-fg text-bg hover:opacity-85 disabled:opacity-30"
            >
              <ArrowUp size={19} strokeWidth={2.25} aria-hidden />
            </button>
          )}
      </div>
    </div>
  );

  if (hero) return box;

  return (
    <div className="px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2">
      <div className="mx-auto w-full max-w-[820px]">{box}</div>
      <p className="mx-auto mt-1.5 max-w-[820px] text-center text-[11px] text-fg-muted">
        Enter to send, Shift+Enter for a new line
      </p>
    </div>
  );
});
