"use client";

import React, { useState } from "react";
import { Cpu, Lock } from "lucide-react";

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
    <div className="flex min-h-screen items-center justify-center bg-[#090b10] px-4 py-10 text-slate-100">
      <div className="w-full max-w-md rounded-2xl border border-slate-700/50 bg-[#101520] p-7 shadow-2xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-sky-400/40 bg-sky-500/15 text-sky-300">
            <Cpu className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-slate-100">
              Media Intelligence OS
            </h1>
            <p className="text-xs text-slate-400">
              Local Operator Sign-In • Phase 4 Control Plane
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
              className="w-full rounded-lg border border-slate-700 bg-[#090b10] px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-400 focus:outline-none"
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
              className="w-full rounded-lg border border-slate-700 bg-[#090b10] px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-400 focus:outline-none"
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
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-sky-400/40 bg-sky-500/20 px-4 py-2.5 text-sm font-semibold text-sky-100 transition-colors hover:bg-sky-500/30 disabled:opacity-50"
          >
            <Lock className="h-4 w-4" aria-hidden="true" />
            <span>{submitting ? "Signing in..." : "Sign in to Control Plane"}</span>
          </button>
        </form>

        <p className="mt-5 border-t border-slate-800 pt-4 text-[11px] leading-relaxed text-slate-400">
          Single-owner local authentication via HttpOnly signed session cookies, PostgreSQL
          session revocation, Redis rate limiting, and CSRF verification. Public self-registration is
          disabled.
        </p>
      </div>
    </div>
  );
}
