"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export type SelectOption<T extends string> = { value: T; label: string };

/**
 * A listbox in the app's own style. The native <select> opens the operating
 * system's menu (square, highlighted in system blue), which breaks the look of
 * every dialog it sits in. Keyboard: arrows, Home/End, Enter/Space, Escape,
 * and typing a letter jumps to the option that starts with it.
 */
export function Select<T extends string>({
  id,
  value,
  options,
  onChange,
  className,
}: {
  id?: string;
  value: T;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
  className?: string;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const current = options.find((o) => o.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    list.current?.focus();
    const away = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open, options, value]);

  const choose = (i: number) => {
    onChange(options[i].value);
    setOpen(false);
    button.current?.focus();
  };

  const onListKey = (e: React.KeyboardEvent) => {
    const last = options.length - 1;
    const keys: Record<string, () => void> = {
      ArrowDown: () => setActive((a) => Math.min(last, a + 1)),
      ArrowUp: () => setActive((a) => Math.max(0, a - 1)),
      Home: () => setActive(0),
      End: () => setActive(last),
      Enter: () => choose(active),
      " ": () => choose(active),
      Escape: () => {
        setOpen(false);
        button.current?.focus();
      },
      Tab: () => setOpen(false),
    };
    if (keys[e.key]) {
      if (e.key !== "Tab") e.preventDefault();
      // Escape closes this menu only, never the dialog around it.
      if (e.key === "Escape") e.stopPropagation();
      keys[e.key]();
      return;
    }
    if (e.key.length === 1) {
      const i = options.findIndex((o) => o.label.toLowerCase().startsWith(e.key.toLowerCase()));
      if (i >= 0) setActive(i);
    }
  };

  return (
    <div ref={root} className={`relative ${className ?? ""}`}>
      <button
        ref={button}
        id={id}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={`flex w-full items-center justify-between gap-2 rounded-[10px] border bg-bg px-3 py-2.5 text-left text-sm text-fg outline-none transition focus-visible:border-brand-blue focus-visible:ring-2 focus-visible:ring-brand-blue/20 ${
          open ? "border-brand-blue ring-2 ring-brand-blue/20" : "border-border hover:border-fg-muted/40"
        }`}
      >
        <span className="truncate">{current?.label}</span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-fg-muted transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>

      {open && (
        <ul
          ref={list}
          id={listId}
          role="listbox"
          tabIndex={-1}
          aria-labelledby={id}
          aria-activedescendant={`${listId}-${active}`}
          onKeyDown={onListKey}
          className="select-pop absolute top-[calc(100%+6px)] right-0 left-0 z-50 max-h-64 overflow-auto rounded-[14px] border border-border bg-surface p-1.5 shadow-[0_18px_40px_-12px_rgba(0,0,0,0.45)] outline-none"
        >
          {options.map((o, i) => {
            const selected = o.value === value;
            return (
              <li
                key={o.value}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={selected}
                onPointerEnter={() => setActive(i)}
                onClick={() => choose(i)}
                className={`flex cursor-pointer items-center justify-between gap-2 rounded-[9px] px-2.5 py-2 text-sm transition-colors ${
                  i === active ? "bg-surface-2 text-fg" : "text-fg-muted"
                } ${selected ? "font-medium text-fg" : ""}`}
              >
                {o.label}
                {selected && <Check className="h-4 w-4 text-brand-blue" aria-hidden />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
