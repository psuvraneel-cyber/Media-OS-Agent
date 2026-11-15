"use client";

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LogOut, Menu, PanelLeftClose, PanelLeftOpen, ShieldAlert, X } from "lucide-react";

import { BrandSwitcher } from "@/components/shell/BrandSwitcher";
import { DesktopChrome } from "@/components/shell/DesktopChrome";
import { NavIcon } from "@/components/shell/NavIcon";
import { RouteProgress, useScrolledAttribute } from "@/components/shell/RouteProgress";
import { Badge } from "@/components/ui/Badge";
import { AmbientBackground, useSurfaceSpotlight } from "@/components/ui/Ambient";
import { ConfirmActionDialog } from "@/components/ui/ConfirmActionDialog";
import { useNow } from "@/lib/use-now";
import { browserApiFetch } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";
import { findRouteMeta, NAV_GROUPS, NAV_ROUTES } from "@/lib/navigation";
import type { SessionUser } from "@/lib/types";

interface OperatorShellProps {
  sessionUser: SessionUser;
  children: React.ReactNode;
}

function LogoMark() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
      <path d="M4 18V6l8 6 8-6v12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

const IST_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const IST_DAY = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  weekday: "short",
  day: "numeric",
  month: "short",
});

/** Live IST clock chip; renders nothing until mounted so SSR and hydration agree. */
function LiveClock() {
  const now = useNow();
  if (now === null) return null;
  const date = new Date(now);
  const [hh, mm, ss] = IST_TIME.format(date).split(":");
  return (
    <span
      className="hidden items-center gap-2 glass-chip rounded-lg px-2.5 py-1.5 text-[11px] text-slate-400 md:inline-flex"
      title="Asia/Kolkata — the brand publishing timezone"
    >
      <span className="text-slate-500">{IST_DAY.format(date)}</span>
      <span className="h-3 w-px bg-white/[0.08]" aria-hidden="true" />
      <span className="font-mono text-slate-200 tabular">
        {hh}:{mm}
        <span className="text-slate-500">:</span>
        <span key={ss} className="tick-in text-sky-300">
          {ss}
        </span>
      </span>
      <span className="text-[10px] font-medium tracking-wide text-slate-500">IST</span>
    </span>
  );
}

const COLLAPSE_KEY = "media-os.sidebar-collapsed";
const collapseListeners = new Set<() => void>();

function subscribeCollapse(listener: () => void) {
  collapseListeners.add(listener);
  return () => collapseListeners.delete(listener);
}

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false; // private mode / blocked storage: stay expanded
  }
}

function writeCollapsed(value: boolean) {
  try {
    window.localStorage.setItem(COLLAPSE_KEY, value ? "1" : "0");
  } catch {
    /* storage unavailable: the toggle simply won't persist */
  }
  collapseListeners.forEach((l) => l());
}

// useLayoutEffect warns during SSR; fall back to useEffect there.
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * Tracks the active nav link's box inside a nav element so a single highlight can glide between
 * items instead of jumping. Re-measures on route change, collapse toggle and resize.
 */
function useNavIndicator(pathname: string, deps: unknown[]) {
  const navRef = useRef<HTMLElement | null>(null);
  const [box, setBox] = useState<{ top: number; height: number } | null>(null);

  const measure = useCallback(() => {
    const nav = navRef.current;
    const active = nav?.querySelector<HTMLElement>('a[aria-current="page"]');
    setBox(active ? { top: active.offsetTop, height: active.offsetHeight } : null);
  }, []);

  useIsoLayoutEffect(() => {
    measure();
  }, [pathname, measure, ...deps]);

  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  return { navRef, box };
}

export function OperatorShell({ sessionUser, children }: OperatorShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { activeBrand, buildHrefWithBrand } = useBrandContext();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  // Per-browser preference; the server snapshot is always "expanded" so hydration matches.
  const collapsed = useSyncExternalStore(subscribeCollapse, readCollapsed, () => false);

  function toggleCollapsed() {
    writeCollapsed(!collapsed);
  }

  const desktopIndicator = useNavIndicator(pathname, [collapsed]);
  const mobileIndicator = useNavIndicator(pathname, [mobileMenuOpen]);

  const currentRoute = findRouteMeta(pathname);
  const currentGroup = NAV_GROUPS.find((g) => g.id === currentRoute.group);
  useSurfaceSpotlight();
  const headerRef = useRef<HTMLElement>(null);
  useScrolledAttribute(headerRef);

  useEffect(() => {
    if (!mobileMenuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileMenuOpen]);

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await browserApiFetch("/auth/logout", {
        method: "POST",
        csrfToken: sessionUser.csrf_token,
      });
    } catch {
      // Even if upstream failed, move user back to login view so cookie checks re-run.
    } finally {
      setSigningOut(false);
      setLogoutConfirmOpen(false);
      router.replace("/login");
    }
  }


  function renderSidebarContent(variant: "desktop" | "mobile") {
    const isMobile = variant === "mobile";
    const compact = !isMobile && collapsed;
    const { navRef, box } = isMobile ? mobileIndicator : desktopIndicator;
    return (
      <div className="flex h-full flex-col">
        <div
          className={`app-drag flex h-[var(--header-height)] items-center ${compact ? "justify-center px-2" : "justify-between px-4"}`}
        >
          <Link
            href={buildHrefWithBrand("/dashboard")}
            onClick={() => setMobileMenuOpen(false)}
            className="group flex items-center gap-2.5 rounded-md text-slate-100"
            title={compact ? "Media OS" : undefined}
          >
            <span className="btn-shine relative flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-sky-200 via-sky-400 to-sky-600 text-[#04121a] shadow-[0_0_0_1px_rgba(34,211,238,0.45),0_6px_18px_-6px_rgba(34,211,238,0.8)] transition-transform duration-300 group-hover:scale-105">
              <LogoMark />
            </span>
            <span className={compact ? "sr-only" : "leading-tight"}>
              <span className="font-display block text-[15px] font-semibold tracking-tight">Media OS</span>
              <span className="block text-[10px] text-slate-500">Intelligence control plane</span>
            </span>
          </Link>
          {isMobile && (
            <button
              type="button"
              onClick={() => setMobileMenuOpen(false)}
              aria-label="Close navigation menu"
              className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-white/[0.06] hover:text-slate-100 lg:hidden"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Active workspace card */}
        <div className={compact ? "flex justify-center px-2 pb-1" : "px-3 pb-1"}>
          <div
            className={`glass-chip flex items-center rounded-xl ${compact ? "p-1" : "gap-2.5 px-2.5 py-2"}`}
            title={compact ? activeBrand.name : undefined}
          >
            <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-sky-300/35 to-indigo-400/30 text-[11px] font-semibold text-sky-50 shadow-[inset_0_1px_0_rgba(255,255,255,0.2)]">
              {activeBrand.name
                .split(/\s+/)
                .filter((w) => /^[A-Za-z]/.test(w))
                .slice(0, 2)
                .map((w) => w[0])
                .join("")
                .toUpperCase() || "B"}
              <span
                aria-hidden="true"
                className={`absolute -right-0.5 -bottom-0.5 h-2.5 w-2.5 rounded-full border-2 border-[#0b0d11] ${activeBrand.active ? "bg-emerald-400" : "bg-slate-500"}`}
              />
            </span>
            <span className={compact ? "sr-only" : "min-w-0 leading-tight"}>
              <span className="eyebrow block text-[9px] text-slate-500">Workspace</span>
              <span className="bn block truncate text-[12px] font-medium text-slate-100">{activeBrand.name}</span>
            </span>
          </div>
        </div>

        <nav
          ref={navRef}
          aria-label="Primary sections"
          className={`relative flex-1 space-y-5 overflow-x-hidden overflow-y-auto py-3 ${compact ? "px-2" : "px-2.5"}`}
        >
          {/* One highlight that glides to the active item. */}
          {box && (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute right-2.5 left-2.5 rounded-md border border-white/[0.06] bg-gradient-to-r from-sky-400/[0.12] via-white/[0.04] to-white/[0.02] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition-[top,height] duration-300 ease-[cubic-bezier(0.34,1.25,0.64,1)] motion-reduce:transition-none"
              style={{ top: box.top, height: box.height, left: compact ? 8 : undefined, right: compact ? 8 : undefined }}
            >
              <span className="absolute top-1.5 bottom-1.5 left-0 w-[2px] rounded-full bg-sky-400 shadow-[0_0_8px_rgba(34,211,238,0.9)]" />
            </span>
          )}
          {NAV_GROUPS.map((group) => {
            const items = NAV_ROUTES.filter((item) => item.group === group.id);
            return (
              <div key={group.id}>
                {compact ? (
                  <div aria-hidden="true" className="mx-auto mb-2 h-px w-6 bg-white/[0.06]" />
                ) : (
                  <p className="mb-1 px-2.5 text-[10px] font-medium tracking-[0.08em] text-slate-500 uppercase">
                    {group.label}
                  </p>
                )}
                <ul className="space-y-px">
                  {items.map((item) => {
                    const isActive = pathname === item.href;
                    return (
                      <li key={item.id}>
                        <Link
                          href={buildHrefWithBrand(item.href)}
                          aria-current={isActive ? "page" : undefined}
                          onClick={() => setMobileMenuOpen(false)}
                          title={compact ? item.label : undefined}
                          className={`group relative z-10 flex items-center rounded-md py-[7px] text-[13px] transition-colors duration-150 ${
                            compact ? "justify-center px-0" : "gap-2.5 px-2.5"
                          } ${
                            isActive
                              ? "font-medium text-slate-50"
                              : "text-slate-400 hover:bg-white/[0.035] hover:text-slate-100"
                          }`}
                        >
                          <span
                            className={`transition-[color,transform] duration-200 group-hover:scale-110 ${isActive ? "text-sky-300" : "text-slate-500 group-hover:text-slate-300"}`}
                          >
                            <NavIcon name={item.iconName} />
                          </span>
                          <span className={compact ? "sr-only" : ""}>{item.label}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>

        <div className={`border-t border-white/[0.06] ${compact ? "p-2" : "p-2.5"}`}>
          {!isMobile && (
            <button
              type="button"
              onClick={toggleCollapsed}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className={`mb-1 flex w-full items-center gap-2.5 rounded-md py-1.5 text-[12px] text-slate-500 transition-colors hover:bg-white/[0.035] hover:text-slate-200 ${compact ? "justify-center px-0" : "px-2.5"}`}
            >
              {collapsed ? (
                <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
              ) : (
                <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
              )}
              {!compact && <span>Collapse</span>}
            </button>
          )}
          <div className={`flex items-center rounded-lg py-2 ${compact ? "flex-col gap-2 px-0" : "gap-2.5 px-2"}`}>
            <span className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-sky-400/30 to-indigo-400/30 p-px">
              <span className="flex h-full w-full items-center justify-center rounded-full bg-[var(--surface-elevated)] text-[11px] font-semibold text-slate-100 uppercase">
                {sessionUser.username.slice(0, 2)}
              </span>
              <span
                aria-hidden="true"
                className="absolute -right-0.5 -bottom-0.5 h-2.5 w-2.5 rounded-full border-2 border-[var(--surface-sidebar)] bg-emerald-400"
              />
            </span>
            <div className={compact ? "sr-only" : "min-w-0 flex-1"}>
              <p className="truncate text-xs font-medium text-slate-200">{sessionUser.username}</p>
              <p className="text-[10px] text-slate-500">Owner · session protected</p>
            </div>
            <button
              type="button"
              onClick={() => setLogoutConfirmOpen(true)}
              aria-label="Sign out"
              title="Sign out"
              className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-rose-400/10 hover:text-rose-300"
            >
              <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="sr-only">Sign out</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-screen text-slate-100">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <AmbientBackground />
      <RouteProgress />

      <div className="relative z-[2] flex min-h-screen">
        <aside
          className={`app-sidebar sticky top-0 hidden h-screen shrink-0 border-r border-white/[0.06] bg-[var(--surface-sidebar)]/25 backdrop-blur-2xl backdrop-saturate-150 transition-[width] duration-300 ease-[cubic-bezier(0.2,0,0,1)] lg:block ${
            collapsed ? "w-16" : "w-[var(--sidebar-width)]"
          }`}
        >
          {renderSidebarContent("desktop")}
        </aside>

        {mobileMenuOpen && (
          <div className="fixed inset-0 z-40 flex lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <div
              className="animate-fade-in fixed inset-0 bg-black/60 backdrop-blur-sm"
              onClick={() => setMobileMenuOpen(false)}
              aria-hidden="true"
            />
            <aside className="animate-slide-in-left relative z-50 h-full w-72 max-w-[85vw] border-r border-white/[0.08] bg-[var(--surface-sidebar)]/70 backdrop-blur-2xl shadow-[var(--shadow-lg)]">
              {renderSidebarContent("mobile")}
            </aside>
          </div>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          <header
            ref={headerRef}
            className="app-header sticky top-0 z-20 border-b border-white/[0.06] bg-[var(--surface-canvas)]/30 backdrop-blur-2xl backdrop-saturate-150"
          >
            <div className="mx-auto flex min-h-[var(--header-height)] w-full max-w-[var(--content-max)] flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2 sm:px-6 lg:px-8">
              <div className="flex min-w-0 items-center gap-3">
                <button
                  type="button"
                  onClick={() => setMobileMenuOpen(true)}
                  aria-label="Open navigation menu"
                  className="rounded-md border border-white/[0.08] p-2 text-slate-300 transition-colors hover:bg-white/[0.05] lg:hidden"
                >
                  <Menu className="h-4 w-4" />
                </button>
                <nav key={pathname} aria-label="Breadcrumb" className="animate-fade-in min-w-0">
                  <ol className="flex min-w-0 items-center gap-2 text-[13px]">
                    <li className="hidden text-slate-500 sm:block">{currentGroup?.label}</li>
                    <li aria-hidden="true" className="hidden text-slate-700 sm:block">
                      /
                    </li>
                    <li className="flex min-w-0 items-center gap-2 font-medium text-slate-100" aria-current="page">
                      <span className="text-sky-300">
                        <NavIcon name={currentRoute.iconName} className="h-3.5 w-3.5" />
                      </span>
                      <span className="truncate">{currentRoute.label}</span>
                    </li>
                  </ol>
                </nav>
              </div>

              <div
                data-testid="active-brand-banner"
                className="flex flex-wrap items-center gap-2"
              >
                <LiveClock />
                {!activeBrand.active && <Badge tone="disabled">Inactive</Badge>}
                <BrandSwitcher />
                <DesktopChrome />
              </div>
            </div>
            {/* Hairline accent under the header */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-x-0 -bottom-px h-px bg-gradient-to-r from-transparent via-sky-400/20 to-transparent"
            />
            {activeBrand.placeholder_unconfigured && (
              <p className="flex items-center gap-1.5 border-t border-amber-400/10 bg-amber-400/[0.04] px-4 py-1.5 text-[11px] text-amber-300/90 sm:px-6 lg:px-8">
                <ShieldAlert className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>
                  {activeBrand.name}: App name, logo & features unconfigured
                </span>
              </p>
            )}
          </header>

          <main
            id="main-content"
            key={pathname}
            className="page-stagger mx-auto w-full max-w-[var(--content-max)] flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8"
          >
            {currentRoute.id !== "dashboard" && (
              <header className="mb-7 flex items-start gap-4">
                <span className="relative mt-0.5 hidden h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/[0.14] bg-gradient-to-br from-sky-300/25 via-sky-400/10 to-indigo-400/20 text-sky-200 shadow-[0_12px_32px_-12px_rgba(34,211,238,0.7),inset_0_1px_0_rgba(255,255,255,0.2)] sm:flex">
                  <NavIcon name={currentRoute.iconName} className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  <p className="eyebrow text-sky-300/80">{currentGroup?.label}</p>
                  <h1 className="text-gradient mt-1 text-[1.75rem] leading-tight font-semibold sm:text-[2rem]">
                    {currentRoute.label}
                  </h1>
                  <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-400">{currentRoute.shortDesc}</p>
                </div>
              </header>
            )}
            {children}
            <footer className="mt-12 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-5 text-[11px] text-slate-500">
              <span className="flex items-center gap-2">
                <span className="font-display font-semibold text-slate-300">Media OS</span>
                <span className="h-3 w-px bg-white/[0.1]" aria-hidden="true" />
                Intelligence control plane
              </span>
              <span>All times in IST (Asia/Kolkata) · Local-first · Owner session protected</span>
            </footer>
          </main>
        </div>
      </div>

      <ConfirmActionDialog
        open={logoutConfirmOpen}
        title="Sign out of Media Intelligence OS?"
        description="Your current local owner session will be revoked on the API server and your browser cookies cleared."
        confirmLabel="Sign out"
        pending={signingOut}
        onConfirm={handleSignOut}
        onCancel={() => setLogoutConfirmOpen(false)}
      />
    </div>
  );
}
