"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, LayoutDashboard, MessageSquare, Search, ShieldCheck, Users } from "lucide-react";
import { api, isUnauthorized } from "@/lib/api";
import type { User } from "@/lib/types";
import { useTheme } from "@/lib/theme";
import { BrandLogo } from "@/components/BrandLogo";
import { TravelLoader } from "@/components/TravelLoader";
import { AdminUserContext, FOCUS } from "@/components/admin/AdminUi";
import { fullNameOf } from "@/lib/admin";

const NAV = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard, match: (p: string) => p === "/admin" },
  {
    href: "/admin/users",
    label: "Users",
    icon: Users,
    match: (p: string) => p.startsWith("/admin/users") || p.startsWith("/admin/chats"),
  },
  { href: "/admin/searches", label: "Searches", icon: Search, match: (p: string) => p.startsWith("/admin/searches") },
];

/**
 * The admin shell. Shown only to accounts the server marks `is_admin`;
 * anyone else is sent to the chat. This guard is for the screen only: every
 * /admin-api call is checked on the server and answers 404 to non-admins.
 */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "/admin";
  const [user, setUser] = useState<User | null>(null);
  useTheme(); // keeps the chosen theme (and "system") applied here too

  useEffect(() => {
    let live = true;
    api
      .me()
      .then(({ user }) => {
        if (!live) return;
        if (!user.is_admin) router.replace("/chat");
        else setUser(user);
      })
      .catch((e) => {
        if (!live) return;
        router.replace(isUnauthorized(e) ? "/login" : "/chat");
      });
    return () => {
      live = false;
    };
  }, [router]);

  if (!user) {
    return (
      <main className="grid min-h-dvh place-items-center bg-bg" aria-busy>
        <TravelLoader size="lg" label="Loading" />
      </main>
    );
  }

  const navLink = (n: (typeof NAV)[number], compact = false) => {
    const active = n.match(pathname);
    const Icon = n.icon;
    return (
      <Link
        key={n.href}
        href={n.href}
        aria-current={active ? "page" : undefined}
        className={`flex items-center gap-2.5 rounded-lg text-sm transition ${FOCUS} ${
          compact ? "shrink-0 px-3 py-1.5" : "w-full px-2.5 py-2"
        } ${
          active
            ? "bg-brand-blue/20 font-medium text-sidebar-fg"
            : "text-sidebar-fg/85 hover:bg-sidebar-2 hover:text-sidebar-fg"
        }`}
      >
        <Icon size={compact ? 15 : 17} aria-hidden />
        {n.label}
      </Link>
    );
  };

  return (
    <AdminUserContext.Provider value={user}>
      <div className="flex min-h-dvh bg-bg text-fg">
        {/* Desktop: a fixed column like the chat sidebar. */}
        <aside className="sticky top-0 hidden h-dvh w-[240px] shrink-0 flex-col self-start border-r border-sidebar-border bg-sidebar text-sidebar-fg lg:flex">
          <div className="flex h-16 shrink-0 items-center justify-center pt-2">
            <BrandLogo />
          </div>
          <p className="mt-3 flex items-center gap-1.5 px-5 text-xs font-medium text-sidebar-muted">
            <ShieldCheck size={13} aria-hidden />
            Admin
          </p>
          <nav aria-label="Admin" className="mt-1 space-y-0.5 px-3">
            {NAV.map((n) => navLink(n))}
          </nav>
          <div className="mt-auto space-y-0.5 border-t border-sidebar-border px-3 py-2">
            <Link
              href="/chat"
              className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-sidebar-fg/85 hover:bg-sidebar-2 hover:text-sidebar-fg ${FOCUS}`}
            >
              <MessageSquare size={17} aria-hidden />
              Back to chat
            </Link>
            <div className="flex items-center gap-2.5 px-2.5 py-1.5">
              <span
                aria-hidden
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-red text-sm font-semibold text-white"
              >
                {fullNameOf(user)[0]?.toUpperCase()}
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{fullNameOf(user)}</p>
                <p className="truncate text-[11px] text-sidebar-muted">{user.email}</p>
              </div>
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* Phones and tablets: a slim bar plus the sections as tabs, kept on
              screen while the page scrolls. The page itself scrolls (not an
              inner box) so browser find, print and full-page captures work. */}
          <div className="sticky top-0 z-30 shrink-0 border-b border-sidebar-border bg-sidebar pt-[env(safe-area-inset-top)] text-sidebar-fg lg:hidden">
            <div className="flex h-14 items-center justify-between gap-3 px-4">
              <div className="flex min-w-0 items-center gap-2.5">
                <BrandLogo />
                <span className="inline-flex items-center gap-1 rounded-full bg-brand-blue/20 px-2 py-0.5 text-[11px] font-medium">
                  <ShieldCheck size={12} aria-hidden />
                  Admin
                </span>
              </div>
              <Link
                href="/chat"
                aria-label="Back to chat"
                className={`inline-flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm text-sidebar-muted hover:bg-sidebar-2 hover:text-sidebar-fg ${FOCUS}`}
              >
                <ArrowLeft size={16} aria-hidden />
                Chat
              </Link>
            </div>
            <nav aria-label="Admin" className="no-scrollbar flex gap-1 overflow-x-auto px-3 pb-2">
              {NAV.map((n) => navLink(n, true))}
            </nav>
          </div>

          <main className="min-w-0 flex-1 pb-[env(safe-area-inset-bottom)]">
            <div className="mx-auto w-full max-w-[1200px] min-w-0 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</div>
          </main>
        </div>
      </div>
    </AdminUserContext.Provider>
  );
}
