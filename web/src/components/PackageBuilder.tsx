"use client";

import { useEffect, useRef, useState } from "react";
import { BedDouble, CarFront, FileCheck2, Map, Minus, Plane, Plus, Star, UtensilsCrossed, X, type LucideIcon } from "lucide-react";
import { Select } from "@/components/Select";
import {
  PACKAGE_SERVICES,
  buildPackageRequest,
  defaultPackageForm,
  packageFormErrors,
  type PackageForm,
  type PackageService,
} from "@/lib/packageBuilder";

const ICONS: Record<PackageService, LucideIcon> = {
  hotel: BedDouble,
  tours: Map,
  transfers: CarFront,
  flights: Plane,
  meals: UtensilsCrossed,
  visa: FileCheck2,
};

const FIELD =
  "w-full rounded-[10px] border border-border bg-bg px-3 py-2.5 text-sm text-fg outline-none transition focus:border-brand-blue focus:ring-2 focus:ring-brand-blue/20";
const LABEL = "mb-1.5 block text-sm font-medium text-fg";
const AGES = Array.from({ length: 18 }, (_, a) => ({ value: String(a), label: a === 0 ? "Under 1" : `${a}` }));

function Stepper({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  const btn =
    "press grid h-9 w-9 place-items-center rounded-lg border border-border text-fg hover:bg-surface-2 disabled:pointer-events-none disabled:opacity-40";
  return (
    <div>
      <span className={LABEL}>{label}</span>
      <div className="flex items-center gap-2" role="group" aria-label={label}>
        <button type="button" className={btn} onClick={() => onChange(value - 1)} disabled={value <= min} aria-label={`Fewer ${label.toLowerCase()}`}>
          <Minus size={16} aria-hidden />
        </button>
        <span className="w-8 text-center text-sm font-semibold tabular-nums text-fg" aria-live="polite">
          {value}
        </span>
        <button type="button" className={btn} onClick={() => onChange(value + 1)} disabled={value >= max} aria-label={`More ${label.toLowerCase()}`}>
          <Plus size={16} aria-hidden />
        </button>
      </div>
    </div>
  );
}

/**
 * The website's "Build package" fields as a form. Submitting sends one plain
 * request in Plan mode; the agent searches each service, prices every part
 * live and replies with the day-by-day package and its total.
 */
export function PackageBuilder({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (request: string) => void;
}) {
  const [form, setForm] = useState<PackageForm>(() => defaultPackageForm());
  const [tried, setTried] = useState(false);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setTried(false);
    first.current?.focus();
  }, [open]);

  if (!open) return null;
  const set = <K extends keyof PackageForm>(k: K, v: PackageForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const errors = packageFormErrors(form);
  const has = (s: PackageService) => form.services.includes(s);
  const toggle = (s: PackageService) => set("services", has(s) ? form.services.filter((x) => x !== s) : [...form.services, s]);
  const today = new Date().toISOString().slice(0, 10);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (errors.length) return;
    onSubmit(buildPackageRequest(form));
  };

  return (
    <div className="fixed inset-0 z-50 grid items-end justify-items-center bg-black/55 backdrop-blur-[2px] sm:place-items-center sm:px-4" onClick={onClose}>
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="builder-title"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
        onSubmit={submit}
        noValidate
        className="panel-pop flex max-h-[92dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl border border-border bg-surface pb-[env(safe-area-inset-bottom)] shadow-2xl sm:max-h-[88dvh] sm:rounded-2xl sm:pb-0"
      >
        <div className="flex items-start justify-between gap-3 border-b border-border px-6 pt-5 pb-4">
          <div>
            <h2 id="builder-title" className="text-lg font-semibold tracking-tight">
              Build a package
            </h2>
            <p className="mt-0.5 text-sm text-fg-muted">Pick what to include. Every part is priced live.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="press grid h-9 w-9 shrink-0 place-items-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={18} aria-hidden />
          </button>
        </div>

        <div className="no-scrollbar min-h-0 space-y-5 overflow-y-auto px-6 py-5">
          <fieldset>
            <legend className={LABEL}>Include</legend>
            <div className="flex flex-wrap gap-2">
              {PACKAGE_SERVICES.map(({ id, label }) => {
                const Icon = ICONS[id];
                const on = has(id);
                return (
                  <button
                    key={id}
                    type="button"
                    role="checkbox"
                    aria-checked={on}
                    onClick={() => toggle(id)}
                    className={`press inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors ${
                      on ? "border-brand-blue bg-brand-blue/10 text-fg" : "border-border text-fg-muted hover:text-fg"
                    }`}
                  >
                    <Icon size={15} strokeWidth={1.75} aria-hidden />
                    {label}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className={LABEL}>Destination</span>
              <input ref={first} className={FIELD} value={form.city} onChange={(e) => set("city", e.target.value)} />
            </label>
            <label className="block">
              <span className={LABEL}>Arrival date</span>
              <input type="date" min={today} className={FIELD} value={form.checkIn} onChange={(e) => set("checkIn", e.target.value)} />
            </label>
          </div>

          {has("flights") && (
            <label className="block">
              <span className={LABEL}>Flying from</span>
              <input className={FIELD} placeholder="e.g. Ahmedabad" value={form.from} onChange={(e) => set("from", e.target.value)} />
            </label>
          )}

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stepper label="Nights" value={form.nights} min={1} max={30} onChange={(v) => set("nights", v)} />
            <Stepper label="Adults" value={form.adults} min={1} max={9} onChange={(v) => set("adults", v)} />
            <Stepper
              label="Children"
              value={form.childAges.length}
              min={0}
              max={4}
              onChange={(v) => set("childAges", v > form.childAges.length ? [...form.childAges, 5] : form.childAges.slice(0, v))}
            />
            <Stepper label="Rooms" value={form.rooms} min={1} max={Math.max(1, form.adults)} onChange={(v) => set("rooms", v)} />
          </div>

          {form.childAges.length > 0 && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {form.childAges.map((age, i) => (
                <div key={i}>
                  <span className={LABEL}>Child {i + 1} age</span>
                  <Select
                    value={String(age)}
                    options={AGES}
                    onChange={(v) => set("childAges", form.childAges.map((a, j) => (j === i ? Number(v) : a)))}
                  />
                </div>
              ))}
            </div>
          )}

          {has("hotel") && (
            <fieldset>
              <legend className={LABEL}>Hotel rating</legend>
              <div className="flex flex-wrap gap-2">
                {[3, 4, 5].map((n) => {
                  const on = form.stars.includes(n);
                  return (
                    <button
                      key={n}
                      type="button"
                      role="checkbox"
                      aria-checked={on}
                      onClick={() => set("stars", on ? form.stars.filter((x) => x !== n) : [...form.stars, n])}
                      className={`press inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm ${
                        on ? "border-brand-blue bg-brand-blue/10 text-fg" : "border-border text-fg-muted hover:text-fg"
                      }`}
                    >
                      <Star size={14} aria-hidden className="text-rating" fill="currentColor" />
                      {n} star
                    </button>
                  );
                })}
                <span className="self-center text-xs text-fg-muted">{form.stars.length ? "" : "Any rating"}</span>
              </div>
            </fieldset>
          )}

          {has("visa") && (
            <label className="block">
              <span className={LABEL}>Passport</span>
              <input className={FIELD} value={form.nationality} onChange={(e) => set("nationality", e.target.value)} />
            </label>
          )}

          <label className="block">
            <span className={LABEL}>
              Anything else <span className="font-normal text-fg-muted">(optional)</span>
            </span>
            <textarea
              rows={2}
              className={`${FIELD} resize-none`}
              placeholder="e.g. Vegetarian meals, near Dubai Mall, no early starts"
              value={form.notes}
              onChange={(e) => set("notes", e.target.value)}
            />
          </label>
        </div>

        <div className="border-t border-border px-6 py-4">
          {tried && errors.length > 0 && (
            <ul className="mb-3 space-y-1 text-sm text-brand-red" role="alert">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="press rounded-[10px] px-4 py-2.5 text-sm font-medium text-fg-muted hover:bg-surface-2 hover:text-fg">
              Cancel
            </button>
            <button
              type="submit"
              className="press rounded-[10px] bg-brand-blue px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-blue-dark"
            >
              Build my package
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
