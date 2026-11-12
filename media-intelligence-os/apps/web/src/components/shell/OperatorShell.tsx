"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Cpu, LogOut, Menu, ShieldAlert, X } from "lucide-react";

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

export function OperatorShell({ sessionUser, children }: OperatorShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { activeBrand, buildHrefWithBrand } = useBrandContext();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const currentRoute = findRouteMeta(pathname);

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
      <div className="flex h-full flex-col justify-between">
        <div>
          <div className="flex items-center justify-between border-b border-slate-800 px-4 py-4">
            <Link
              href={buildHrefWithBrand("/dashboard")}
              onClick={() => setMobileMenuOpen(false)}
              className="flex items-center gap-2.5 text-slate-100"
            >
              <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-sky-400/40 bg-sky-500/15 text-sky-300">
                <Cpu className="h-4 w-4" aria-hidden="true" />
              </div>
              <div>
                <p className="text-sm font-semibold tracking-tight">Media OS</p>
                <p className="text-[10px] tracking-wider text-slate-400 uppercase">
                  Phase 4 Control Plane
                </p>
              </div>
            </Link>
            {showMobileClose && (
              <button
                type="button"
                onClick={() => setMobileMenuOpen(false)}
                aria-label="Close navigation menu"
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100 lg:hidden"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>

          <nav aria-label="Primary sections" className="space-y-5 px-3 py-4">
            {NAV_GROUPS.map((group) => {
              const items = NAV_ROUTES.filter((item) => item.group === group.id);
              return (
                <div key={group.id}>
                  <p className="mb-1.5 px-2.5 text-[10px] font-semibold tracking-wider text-slate-500 uppercase">
                    {group.label}
                  </p>
                  <ul className="space-y-0.5">
                    {items.map((item) => {
                      const isActive = pathname === item.href;
                      return (
                        <li key={item.id}>
                          <Link
                            href={buildHrefWithBrand(item.href)}
                            aria-current={isActive ? "page" : undefined}
                            onClick={() => setMobileMenuOpen(false)}
                            className={`flex items-center justify-between rounded-lg px-2.5 py-2 text-xs font-medium transition-colors ${
                              isActive
                                ? "border border-sky-400/35 bg-sky-500/12 text-sky-200"
                                : "border border-transparent text-slate-300 hover:bg-slate-800/70 hover:text-slate-100"
                            }`}
                          >
                            <span className="flex items-center gap-2.5">
                              <NavIcon name={item.iconName} />
                              <span>{item.label}</span>
                            </span>
                            <span
                              className={`text-[10px] ${isActive ? "text-sky-300" : "text-slate-500"}`}
                            >
                              {item.targetPhase.replace(" Target", "")}
                            </span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </nav>
        </div>

        <div className="border-t border-slate-800 p-3">
          <div className="rounded-lg border border-slate-800 bg-[#090b10] p-3 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-slate-200">{sessionUser.username}</span>
              <Badge tone="live">Owner</Badge>
            </div>
            <p className="mt-1 text-[11px] text-slate-400">
              PostgreSQL session revocation &amp; Redis rate limiting active.
            </p>
            <button
              type="button"
              onClick={() => setLogoutConfirmOpen(true)}
              className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-xs font-medium text-slate-200 hover:border-slate-600 hover:bg-slate-800"
            >
              <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Sign out</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#090b10] text-slate-100">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      <div className="flex min-h-screen">
        {/* Desktop sidebar */}
        <aside className="hidden w-64 shrink-0 border-r border-slate-800 bg-[#0d1118] lg:block">
          {renderSidebarContent(false)}
        </aside>

        {/* Mobile slide-over navigation */}
        {mobileMenuOpen && (
          <div className="fixed inset-0 z-40 flex lg:hidden">
            <div
              className="fixed inset-0 bg-black/70"
              onClick={() => setMobileMenuOpen(false)}
              aria-hidden="true"
            />
            <aside className="relative z-50 w-72 border-r border-slate-800 bg-[#0d1118]">
              {renderSidebarContent(true)}
            </aside>
          </div>
        )}

        {/* Main workspace column */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-20 border-b border-slate-800 bg-[#0d1118]/95 px-4 py-3 backdrop-blur-sm sm:px-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setMobileMenuOpen(true)}
                  aria-label="Open navigation menu"
                  className="rounded-lg border border-slate-700/70 bg-slate-800/70 p-2 text-slate-300 hover:bg-slate-800 lg:hidden"
                >
                  <Menu className="h-4 w-4" />
                </button>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h1 className="text-base font-semibold tracking-tight text-slate-100 sm:text-lg">
                      {currentRoute.label}
                    </h1>
                    <Badge
                      tone={
                        currentRoute.href === "/dashboard" ||
                        currentRoute.href === "/logs" ||
                        currentRoute.href === "/settings"
                          ? "live"
                          : "info"
                      }
                    >
                      {currentRoute.targetPhase}
                    </Badge>
                  </div>
                  <p className="text-xs text-slate-400">{currentRoute.shortDesc}</p>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2.5">
                <BrandSwitcher />
              </div>
            </div>

            {/* Active brand isolation notice strip */}
            <div
              data-testid="active-brand-banner"
              className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-[#090b10] px-3 py-2 text-xs"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-sky-300">{activeBrand.name}</span>
                {!activeBrand.active && <Badge tone="disabled">Inactive</Badge>}
                <span className="text-slate-500">•</span>
                <span className="text-slate-300">{activeBrand.niche}</span>
              </div>
              {activeBrand.placeholder_unconfigured ? (
                <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-amber-300">
                  <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>App name, logo & features unconfigured</span>
                </span>
              ) : (
                <span className="text-[11px] text-slate-400">
                  Isolated workspace context (Approval: Human Required)
                </span>
              )}
            </div>
          </header>

          <main id="main-content" className="mx-auto w-full max-w-7xl flex-1 p-4 sm:p-6">
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
