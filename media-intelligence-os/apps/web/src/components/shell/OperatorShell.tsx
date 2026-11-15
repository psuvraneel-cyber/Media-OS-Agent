"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LogOut, Menu, ShieldAlert, X } from "lucide-react";

import { BrandSwitcher } from "@/components/shell/BrandSwitcher";
import { NavIcon } from "@/components/shell/NavIcon";
import { Badge } from "@/components/ui/Badge";
import { ConfirmActionDialog } from "@/components/ui/ConfirmActionDialog";
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

export function OperatorShell({ sessionUser, children }: OperatorShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { activeBrand, buildHrefWithBrand } = useBrandContext();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const currentRoute = findRouteMeta(pathname);

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

  function renderSidebarContent(showMobileClose: boolean) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex h-[var(--header-height)] items-center justify-between px-4">
          <Link
            href={buildHrefWithBrand("/dashboard")}
            onClick={() => setMobileMenuOpen(false)}
            className="flex items-center gap-2.5 rounded-md text-slate-100"
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-b from-sky-300 to-sky-500 text-[#04121a] shadow-[0_0_0_1px_rgba(34,211,238,0.4)]">
              <LogoMark />
            </span>
            <span className="leading-tight">
              <span className="block text-[13px] font-semibold tracking-tight">Media OS</span>
              <span className="block text-[10px] text-slate-500">Intelligence control plane</span>
            </span>
          </Link>
          {showMobileClose && (
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

        <nav aria-label="Primary sections" className="flex-1 space-y-5 overflow-y-auto px-2.5 py-3">
          {NAV_GROUPS.map((group) => {
            const items = NAV_ROUTES.filter((item) => item.group === group.id);
            return (
              <div key={group.id}>
                <p className="mb-1 px-2.5 text-[10px] font-medium tracking-[0.08em] text-slate-500 uppercase">
                  {group.label}
                </p>
                <ul className="space-y-px">
                  {items.map((item) => {
                    const isActive = pathname === item.href;
                    return (
                      <li key={item.id}>
                        <Link
                          href={buildHrefWithBrand(item.href)}
                          aria-current={isActive ? "page" : undefined}
                          onClick={() => setMobileMenuOpen(false)}
                          className={`group relative flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-[13px] transition-colors duration-150 ${
                            isActive
                              ? "bg-white/[0.06] font-medium text-slate-50"
                              : "text-slate-400 hover:bg-white/[0.035] hover:text-slate-100"
                          }`}
                        >
                          {isActive && (
                            <span
                              aria-hidden="true"
                              className="absolute top-1.5 bottom-1.5 left-0 w-[2px] rounded-full bg-sky-400"
                            />
                          )}
                          <span
                            className={`transition-colors ${isActive ? "text-sky-300" : "text-slate-500 group-hover:text-slate-300"}`}
                          >
                            <NavIcon name={item.iconName} />
                          </span>
                          <span>{item.label}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>

        <div className="border-t border-white/[0.06] p-2.5">
          <div className="flex items-center gap-2.5 rounded-lg px-2 py-2">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-white/[0.1] bg-[var(--surface-elevated)] text-[11px] font-semibold text-slate-200 uppercase">
              {sessionUser.username.slice(0, 2)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-slate-200">{sessionUser.username}</p>
              <p className="text-[10px] text-slate-500">Owner · session protected</p>
            </div>
            <button
              type="button"
              onClick={() => setLogoutConfirmOpen(true)}
              aria-label="Sign out"
              title="Sign out"
              className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-white/[0.06] hover:text-slate-200"
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
    <div className="min-h-screen bg-[var(--surface-canvas)] text-slate-100">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      <div className="flex min-h-screen">
        <aside className="sticky top-0 hidden h-screen w-[var(--sidebar-width)] shrink-0 border-r border-white/[0.06] bg-[var(--surface-sidebar)] lg:block">
          {renderSidebarContent(false)}
        </aside>

        {mobileMenuOpen && (
          <div className="fixed inset-0 z-40 flex lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <div
              className="animate-fade-in fixed inset-0 bg-black/60 backdrop-blur-[2px]"
              onClick={() => setMobileMenuOpen(false)}
              aria-hidden="true"
            />
            <aside className="animate-slide-in-left relative z-50 h-full w-72 max-w-[85vw] border-r border-white/[0.06] bg-[var(--surface-sidebar)] shadow-[var(--shadow-lg)]">
              {renderSidebarContent(true)}
            </aside>
          </div>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-20 border-b border-white/[0.06] bg-[var(--surface-canvas)]/85 backdrop-blur-md">
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
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h1 className="truncate text-[15px] font-semibold tracking-tight text-slate-50">
                      {currentRoute.label}
                    </h1>
                    <span className="hidden text-[11px] text-slate-500 sm:inline" title="Delivery phase">
                      {currentRoute.targetPhase}
                    </span>
                  </div>
                  <p className="hidden truncate text-xs text-slate-500 md:block">
                    {currentRoute.shortDesc}
                  </p>
                </div>
              </div>

              <div
                data-testid="active-brand-banner"
                className="flex flex-wrap items-center gap-2"
              >
                {!activeBrand.active && <Badge tone="disabled">Inactive</Badge>}
                <BrandSwitcher />
              </div>
            </div>
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
            className="animate-fade-up mx-auto w-full max-w-[var(--content-max)] flex-1 px-4 py-6 sm:px-6 lg:px-8"
          >
            {children}
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
