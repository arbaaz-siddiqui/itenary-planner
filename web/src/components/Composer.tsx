"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { ArrowUp, Mic, MicOff, Plus, Square, X } from "lucide-react";
import { serviceById, type ServiceId } from "@/lib/services";

/** About six lines; then the box scrolls inside itself. */
const MAX_HEIGHT_PX = 168;

export type ComposerMode = "ask" | "plan";

export interface ComposerHandle {
  /** Drop text into the box (a starter prompt, "Ask the agent about this"). */
  prefill: (text: string) => void;
  focus: () => void;
}

// Minimal typing for the browser speech API, which TypeScript's DOM lib omits.
interface SpeechResultEvent {
  resultIndex: number;
  results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }>;
}
interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: SpeechResultEvent) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start: () => void;
  stop: () => void;
}
type SpeechCtor = new () => SpeechRecognitionLike;

function speechCtor(): SpeechCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: SpeechCtor; webkitSpeechRecognition?: SpeechCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

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
  const [listening, setListening] = useState(false);
  const [canDictate, setCanDictate] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const recog = useRef<SpeechRecognitionLike | null>(null);
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

  useEffect(() => setCanDictate(Boolean(speechCtor())), []);
  useEffect(() => () => recog.current?.stop(), []);

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
    recog.current?.stop();
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

  function toggleMic() {
    if (listening) {
      recog.current?.stop();
      return;
    }
    const Ctor = speechCtor();
    if (!Ctor) return;
    const r = new Ctor();
    r.lang = "en-IN";
    r.interimResults = false;
    r.continuous = false;
    const before = text;
    r.onresult = (e) => {
      let heard = "";
      for (let i = e.resultIndex; i < e.results.length; i += 1) heard += e.results[i][0].transcript;
      setText((before ? `${before} ` : "") + heard.trim());
    };
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    recog.current = r;
    setListening(true);
    r.start();
  }

  const hero = variant === "hero";
  const iconBtn =
    "grid h-9 w-9 shrink-0 place-items-center rounded-full text-fg-muted transition hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-brand-blue";

  const box = (
    <div
      className={`w-full rounded-3xl border border-border bg-surface shadow-lg transition focus-within:border-brand-blue/60 ${
        hero ? "shadow-black/30" : "shadow-black/10"
      }`}
    >
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
      <div className="flex items-center gap-1 px-2 pb-2 pt-1">
        <button
          type="button"
          disabled
          title="Attachments are coming soon"
          aria-label="Add attachment (coming soon)"
          className={`${iconBtn} disabled:cursor-not-allowed disabled:opacity-40`}
        >
          <Plus size={18} aria-hidden />
        </button>
        <div className="flex-1" />
        <div role="radiogroup" aria-label="Mode" className="mr-1 flex rounded-full bg-surface-2 p-0.5 text-xs">
          {(["ask", "plan"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              onClick={() => onModeChange(m)}
              className={`rounded-full px-3 py-1 font-medium capitalize transition ${
                mode === m ? "bg-bg text-fg shadow-sm" : "text-fg-muted hover:text-fg"
              }`}
            >
              {m}
            </button>
          ))}
        </div>
        {canDictate && (
          <button
            type="button"
            onClick={toggleMic}
            aria-label={listening ? "Stop dictation" : "Dictate"}
            aria-pressed={listening}
            title={listening ? "Stop dictation" : "Dictate"}
            className={`${iconBtn} ${listening ? "bg-brand-red/15 text-brand-red" : ""}`}
          >
            {listening ? <MicOff size={18} aria-hidden /> : <Mic size={18} aria-hidden />}
          </button>
        )}
        {streaming ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop generating"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-fg text-bg transition hover:opacity-85"
          >
            <Square size={13} fill="currentColor" aria-hidden />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim() || disabled}
            aria-label="Send message"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-blue text-white transition hover:bg-brand-blue-dark disabled:opacity-40"
          >
            <ArrowUp size={18} aria-hidden />
          </button>
        )}
      </div>
    </div>
  );

  if (hero) return box;

  return (
    <div className="bg-bg px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2">
      <div className="mx-auto w-full max-w-[820px]">{box}</div>
      <p className="mx-auto mt-1.5 max-w-[820px] text-center text-[11px] text-fg-muted">
        Enter to send · Shift+Enter for a new line · Prices are live from GujjuTours
      </p>
    </div>
  );
});
