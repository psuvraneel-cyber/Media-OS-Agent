"use client";

import React, { useState } from "react";
import { Cpu, Lock } from "lucide-react";

import { AmbientBackground } from "@/components/ui/Ambient";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import type { SessionUser } from "@/lib/types";

export function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setErrorMsg(null);
    try {
      await browserApiFetch<SessionUser>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      // Full browser navigation ensures fresh SSR cookie hydration across root layouts.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = "/dashboard";
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 429) {
          setErrorMsg("Too many failed attempts. Wait before trying again.");
        } else {
          setErrorMsg(err.message);
        }
      } else {
        setErrorMsg("Unable to reach authentication service.");
      }
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10 text-slate-100">
      <AmbientBackground />
      {/* Halo behind the card */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 left-1/2 z-[1] h-[34rem] w-[34rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(closest-side,rgba(34,211,238,0.12),transparent)]"
      />
      <div className="glass glow-border animate-scale-in relative z-[2] w-full max-w-md rounded-2xl p-8">
        <div className="mb-7 flex items-center gap-3.5">
          <div className="btn-shine flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-sky-200 via-sky-400 to-sky-600 text-[#04121a] shadow-[0_0_0_1px_rgba(34,211,238,0.45),0_10px_28px_-8px_rgba(34,211,238,0.85)]">
            <Cpu className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-gradient text-xl font-semibold tracking-tight">
              Media Intelligence OS
            </h1>
            <p className="text-xs text-slate-400">
              Local operator sign-in · Intelligence control plane
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="owner-username"
              className="mb-1.5 block text-xs font-medium text-slate-300"
            >
              Owner Username
            </label>
            <input
              id="owner-username"
              name="username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-white/[0.1] bg-black/30 px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-500 transition-[border-color,box-shadow] duration-200 hover:border-white/[0.18] focus:border-sky-400/70 focus:shadow-[0_0_0_4px_rgba(34,211,238,0.12),0_0_20px_-6px_rgba(34,211,238,0.6)] focus:outline-none"
              placeholder="Enter local owner username"
            />
          </div>

          <div>
            <label
              htmlFor="owner-password"
              className="mb-1.5 block text-xs font-medium text-slate-300"
            >
              Password
            </label>
            <input
              id="owner-password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-white/[0.1] bg-black/30 px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-500 transition-[border-color,box-shadow] duration-200 hover:border-white/[0.18] focus:border-sky-400/70 focus:shadow-[0_0_0_4px_rgba(34,211,238,0.12),0_0_20px_-6px_rgba(34,211,238,0.6)] focus:outline-none"
              placeholder="Enter owner password"
            />
          </div>

          {errorMsg && (
            <div
              role="alert"
              className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
            >
              {errorMsg}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="btn-press btn-shine inline-flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-b from-sky-300 to-sky-500 px-4 py-2.5 text-sm font-semibold text-[#04121a] shadow-[0_0_0_1px_rgba(34,211,238,0.5),0_10px_26px_-10px_rgba(34,211,238,0.9)] hover:from-sky-200 hover:to-sky-400 disabled:opacity-50"
          >
            <Lock className="h-4 w-4" aria-hidden="true" />
            <span>{submitting ? "Signing in..." : "Sign in to Control Plane"}</span>
          </button>
        </form>

        <p className="mt-5 border-t border-white/[0.06] pt-4 text-[11px] leading-relaxed text-slate-400">
          Single-owner local authentication via HttpOnly signed session cookies, PostgreSQL
          session revocation, Redis rate limiting, and CSRF verification. Public self-registration is
          disabled.
        </p>
      </div>
    </div>
  );
}
