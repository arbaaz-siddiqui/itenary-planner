"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  HelpCircle,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Settings,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import type { ChatSession, User } from "@/lib/types";
import { SERVICES, displayFirstName, type ServiceId } from "@/lib/services";
import { groupSessions } from "@/lib/sessionGroups";
import { BrandLogo } from "./BrandLogo";
import type { CardRef } from "@/lib/cardActions";
import { titleOf } from "@/lib/cards";
import { KIND_ICON } from "./cards/Cards";

const ROW = "flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm transition";
const ROW_IDLE = "text-sidebar-fg/85 hover:bg-sidebar-2 hover:text-sidebar-fg";
const ROW_ACTIVE = "bg-brand-blue/20 text-sidebar-fg font-medium";
const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue";

function SessionRow({
  session,
  active,
  pinned,
  onSelect,
  onRename,
  onDelete,
  onTogglePin,
}: {
  session: ChatSession;
  active: boolean;
  pinned: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
  onTogglePin: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.title);
  const [confirming, setConfirming] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  function commit() {
    const t = draft.trim();
    setEditing(false);
    if (t && t !== session.title) onRename(t);
    else setDraft(session.title);
  }

  if (editing) {
    return (
      <li>
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setDraft(session.title);
              setEditing(false);
            }
          }}
          aria-label="Chat title"
          className="w-full rounded-lg border border-brand-blue bg-sidebar-2 px-2.5 py-1.5 text-sm text-sidebar-fg outline-none"
        />
      </li>
    );
  }

  const iconBtn =
    "grid h-6 w-6 place-items-center rounded text-sidebar-muted hover:bg-sidebar hover:text-sidebar-fg " + FOCUS;

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "page" : undefined}
        className={`${ROW} py-1.5 pr-2.5 group-hover:pr-20 group-focus-within:pr-20 [@media(hover:none)]:pr-20 ${active ? ROW_ACTIVE : ROW_IDLE} ${FOCUS}`}
      >
        <span className="truncate">{session.title || "Untitled"}</span>
      </button>

      <div
        className={`absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5 ${
          confirming ? "" : "opacity-0 transition group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100"
        }`}
      >
        {confirming ? (
          <>
            <button
              type="button"
              onClick={onDelete}
              className={`rounded px-1.5 py-0.5 text-[11px] font-medium text-brand-red hover:bg-brand-red/15 ${FOCUS}`}
            >
              Delete
            </button>
            <button type="button" onClick={() => setConfirming(false)} aria-label="Cancel delete" className={iconBtn}>
              <X size={13} aria-hidden />
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={onTogglePin}
              aria-label={`${pinned ? "Unpin" : "Pin"} ${session.title}`}
              title={pinned ? "Unpin" : "Pin"}
              className={iconBtn}
            >
              {pinned ? <PinOff size={13} aria-hidden /> : <Pin size={13} aria-hidden />}
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft(session.title);
                setEditing(true);
              }}
              aria-label={`Rename ${session.title}`}
              title="Rename"
              className={iconBtn}
            >
              <Pencil size={13} aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => setConfirming(true)}
              aria-label={`Delete ${session.title}`}
              title="Delete"
              className={iconBtn.replace("hover:text-sidebar-fg", "hover:text-brand-red")}
            >
              <Trash2 size={13} aria-hidden />
            </button>
          </>
        )}
      </div>
    </li>
  );
}

export interface SidebarProps {
  sessions: ChatSession[];
  activeId: string | null;
  user: User | null;
  loading: boolean;
  /** Icon-rail mode. Ignored in the mobile drawer, which is always full. */
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  /** Present in the mobile drawer: closes it. */
  onClose?: () => void;
  pinned: ReadonlySet<string>;
  activeService: ServiceId | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onService: (id: ServiceId) => void;
  onSearch: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onTogglePin: (id: string) => void;
  onSettings: () => void;
  onLogout: () => void;
  themeResolved: "dark" | "light";
  onToggleTheme: () => void;
  /** Cards saved with the heart, newest first. */
  saved: CardRef[];
  onOpenSaved: (card: CardRef) => void;
}

export function Sidebar(p: SidebarProps) {
  const collapsed = Boolean(p.collapsed);
  const groups = useMemo(() => groupSessions(p.sessions, p.pinned), [p.sessions, p.pinned]);
  const name = displayFirstName(p.user) || "Traveller";
  const fullName = [p.user?.first_name, p.user?.last_name].filter(Boolean).join(" ") || name;

  // In the rail every item is an icon with its label as a tooltip.
  const item = (
    key: string,
    label: string,
    Icon: React.ComponentType<{ size?: number; "aria-hidden"?: boolean }>,
    onClick: () => void,
    active = false,
  ) => (
    <button
      key={key}
      type="button"
      onClick={onClick}
      title={collapsed ? label : undefined}
      aria-label={collapsed ? label : undefined}
      aria-current={active ? "true" : undefined}
      className={`${ROW} ${collapsed ? "justify-center px-0" : ""} ${active ? ROW_ACTIVE : ROW_IDLE} ${FOCUS}`}
    >
      <Icon size={17} aria-hidden />
      {!collapsed && <span className="truncate">{label}</span>}
    </button>
  );

  return (
    // On short screens (a phone on its side) the chat list would be squeezed
    // to nothing between the services and the footer, so the whole column
    // scrolls as one instead.
    <div className="no-scrollbar flex h-full flex-col overflow-y-auto bg-sidebar text-sidebar-fg">
      {/* Header: wordmark + collapse toggle (or close, in the drawer). */}
      <div className={`flex h-14 shrink-0 items-center ${collapsed ? "justify-center" : "justify-between px-3"}`}>
        {!collapsed && <BrandLogo />}
        {p.onClose ? (
          <button
            type="button"
            onClick={p.onClose}
            aria-label="Close menu"
            className={`grid h-8 w-8 place-items-center rounded-lg text-sidebar-muted hover:bg-sidebar-2 hover:text-sidebar-fg ${FOCUS}`}
          >
            <X size={18} aria-hidden />
          </button>
        ) : (
          p.onToggleCollapse && (
            <button
              type="button"
              onClick={p.onToggleCollapse}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className={`grid h-8 w-8 place-items-center rounded-lg text-sidebar-muted hover:bg-sidebar-2 hover:text-sidebar-fg ${FOCUS}`}
            >
              {collapsed ? <PanelLeftOpen size={18} aria-hidden /> : <PanelLeftClose size={18} aria-hidden />}
            </button>
          )
        )}
      </div>

      <div className={`shrink-0 space-y-1 ${collapsed ? "px-2" : "px-3"}`}>
        <button
          type="button"
          onClick={p.onNew}
          title={collapsed ? "New chat" : undefined}
          aria-label={collapsed ? "New chat" : undefined}
          className={`press flex w-full items-center gap-2 rounded-lg bg-brand-blue py-2 text-sm font-medium text-white hover:bg-brand-blue-dark ${
            collapsed ? "justify-center px-0" : "px-3"
          } ${FOCUS}`}
        >
          <Plus size={17} aria-hidden />
          {!collapsed && "New chat"}
        </button>
        {collapsed ? (
          item("search", "Search chats (Ctrl+K)", Search, p.onSearch)
        ) : (
          <button type="button" onClick={p.onSearch} className={`${ROW} ${ROW_IDLE} ${FOCUS}`}>
            <Search size={17} aria-hidden />
            <span className="flex-1">Search</span>
            <kbd className="rounded border border-sidebar-border px-1.5 text-[10px] text-sidebar-muted [@media(hover:none)]:hidden">Ctrl K</kbd>
          </button>
        )}
      </div>

      <nav aria-label="Services" className={`mt-4 shrink-0 ${collapsed ? "px-2" : "px-3"}`}>
        {!collapsed && (
          <p className="px-2.5 pb-1 text-xs font-medium text-sidebar-muted">Services</p>
        )}
        <div className="space-y-0.5">
          {SERVICES.map((s) => item(s.id, s.label, s.icon, () => p.onService(s.id), p.activeService === s.id))}
        </div>
      </nav>

      {/* Chats and saved items: hidden in the rail, where a title cannot fit. */}
      <nav aria-label="Chat sessions" className="no-scrollbar mt-4 min-h-0 flex-1 overflow-y-auto px-3 pb-2 [@media(max-height:600px)]:flex-none [@media(max-height:600px)]:overflow-visible">
        {!collapsed && p.saved.length > 0 && (
          <section className="mb-3">
            <p className="px-2.5 pb-1 text-xs font-medium text-sidebar-muted">Saved</p>
            <ul className="space-y-0.5">
              {p.saved.slice(0, 8).map((c) => {
                const Icon = KIND_ICON[c.kind];
                return (
                  <li key={c.id}>
                    <button type="button" onClick={() => p.onOpenSaved(c)} className={`${ROW} py-1.5 ${ROW_IDLE} ${FOCUS}`}>
                      <Icon size={15} className="shrink-0 text-brand-red" aria-hidden />
                      <span className="truncate">{titleOf(c.item, c.kind)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
        {!collapsed &&
          (p.loading ? (
            <p className="px-2.5 py-2 text-xs text-sidebar-muted">Loading chats…</p>
          ) : groups.length === 0 ? (
            <p className="px-2.5 py-2 text-xs text-sidebar-muted">No chats yet.</p>
          ) : (
            groups.map((g) => (
              <section key={g.label} className="mb-3">
                <p className="px-2.5 pb-1 text-xs font-medium text-sidebar-muted">
                  {g.label}
                </p>
                <ul className="space-y-0.5">
                  {g.sessions.map((s) => (
                    <SessionRow
                      key={s.id}
                      session={s}
                      active={s.id === p.activeId}
                      pinned={p.pinned.has(s.id)}
                      onSelect={() => p.onSelect(s.id)}
                      onRename={(t) => p.onRename(s.id, t)}
                      onDelete={() => p.onDelete(s.id)}
                      onTogglePin={() => p.onTogglePin(s.id)}
                    />
                  ))}
                </ul>
              </section>
            ))
          ))}
      </nav>

      <div className={`shrink-0 space-y-0.5 border-t border-sidebar-border py-2 ${collapsed ? "px-2" : "px-3"}`}>
        {item(
          "theme",
          p.themeResolved === "dark" ? "Light theme" : "Dark theme",
          p.themeResolved === "dark" ? Sun : Moon,
          p.onToggleTheme,
        )}
        {item("settings", "Settings", Settings, p.onSettings)}
        {item("help", "Help", HelpCircle, () => window.open("https://www.gujjutours.com/en/contact-us", "_blank", "noopener"))}
        <div className={`flex items-center gap-2.5 rounded-lg py-1.5 ${collapsed ? "justify-center" : "px-2.5"}`}>
          <span
            aria-hidden
            title={collapsed ? p.user?.email : undefined}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-red text-sm font-semibold text-white"
          >
            {name[0]}
          </span>
          {!collapsed && (
            <>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{fullName}</p>
                <p className="truncate text-[11px] text-sidebar-muted">{p.user?.email ?? ""}</p>
              </div>
              <button
                type="button"
                onClick={p.onLogout}
                aria-label="Sign out"
                title="Sign out"
                className={`grid h-8 w-8 place-items-center rounded-lg text-sidebar-muted hover:bg-sidebar-2 hover:text-sidebar-fg ${FOCUS}`}
              >
                <LogOut size={16} aria-hidden />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
