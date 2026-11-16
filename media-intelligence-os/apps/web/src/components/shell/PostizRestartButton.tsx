"use client";

import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, RotateCw, X } from "lucide-react";

import { ApiClientError, browserApiFetch } from "@/lib/api-client";

export interface RestartState {
  id: string | null;
  status: "PENDING" | "RUNNING" | "DONE" | "FAILED" | "EXPIRED" | null;
  requested_at: string | null;
  finished_at: string | null;
  detail: string | null;
  helper_late: boolean;
}

const POLL_MS = 3000;
const GIVE_UP_MS = 5 * 60 * 1000;

/** Plain-words line for where a restart request is. */
export function restartMessage(s: RestartState | null): string | null {
  if (!s || !s.status) return null;
  switch (s.status) {
    case "PENDING":
      return s.helper_late
        ? "Still waiting: the restart helper on your PC hasn't picked this up. Is the PC awake?"
        : "Restart requested. Your PC picks it up within a minute…";
    case "RUNNING":
      return "Restarting Postiz…";
    case "DONE":
      return s.detail ?? "Postiz restarted.";
    case "FAILED":
    case "EXPIRED":
      return s.detail ?? "The restart didn't happen.";
  }
}

/**
 * Small "restart Postiz" icon for the health strip. The click only records a request; the
 * Windows helper task restarts the container within a minute and reports back here.
 */
export function PostizRestartButton() {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [state, setState] = useState<RestartState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const busy = state?.status === "PENDING" || state?.status === "RUNNING";

  async function start() {
    setConfirming(false);
    setError(null);
    const began = Date.now();
    try {
      setState(await browserApiFetch<RestartState>("/system/postiz/restart", { method: "POST" }));
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Couldn't ask for a restart.");
      return;
    }
    const poll = async () => {
      if (!alive.current) return;
      try {
        const s = await browserApiFetch<RestartState>("/system/postiz/restart");
        if (!alive.current) return;
        setState(s);
        if (s.status === "DONE") {
          // Give Postiz a moment to come up, then re-check the gateway status.
          timer.current = setTimeout(() => alive.current && router.refresh(), 20000);
          return;
        }
        if (s.status === "FAILED" || s.status === "EXPIRED") return;
      } catch {
        // keep polling through a brief blip (the API itself stays up during a Postiz restart)
      }
      if (Date.now() - began < GIVE_UP_MS) timer.current = setTimeout(() => void poll(), POLL_MS);
    };
    timer.current = setTimeout(() => void poll(), POLL_MS);
  }

  const message = error ?? restartMessage(state);
  const Icon = state?.status === "DONE" ? Check : RotateCw;

  return (
    <div className="relative">
      {confirming ? (
        <div className="flex items-center gap-1" role="group" aria-label="Confirm Postiz restart">
          <button
            type="button"
            onClick={() => void start()}
            className="rounded-md bg-sky-400/90 px-2 py-0.5 text-[10.5px] font-semibold text-[#04121a] hover:bg-sky-300"
          >
            Restart
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            aria-label="Cancel restart"
            className="rounded-md p-0.5 text-slate-400 hover:bg-white/[0.06] hover:text-slate-100"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={busy}
          aria-label="Restart Postiz"
          title="Restart Postiz (your PC does it within a minute)"
          className="rounded-md p-1 text-slate-500 transition-colors hover:bg-white/[0.06] hover:text-sky-300 disabled:cursor-progress"
        >
          <Icon
            className={`h-3.5 w-3.5 ${busy ? "animate-spin text-sky-300" : ""} ${state?.status === "DONE" ? "text-emerald-300" : ""}`}
            aria-hidden="true"
          />
        </button>
      )}
      {message && (
        <p
          role="status"
          className={`absolute top-full right-0 z-10 mt-1 w-56 rounded-md border border-white/[0.08] bg-[rgba(17,21,29,0.95)] px-2.5 py-1.5 text-[10.5px] leading-snug shadow-lg ${
            error || state?.status === "FAILED" || state?.status === "EXPIRED" || state?.helper_late
              ? "text-amber-200"
              : state?.status === "DONE"
                ? "text-emerald-200"
                : "text-slate-300"
          }`}
        >
          {message}
        </p>
      )}
    </div>
  );
}
