"use client";

import React, { useEffect, useRef, useState } from "react";
import { Check, RefreshCw } from "lucide-react";

import type { SyncStatus } from "@/lib/analytics";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";

const POLL_MS = 3000;
const GIVE_UP_MS = 4 * 60 * 1000;
const FAILED = new Set(["FAILED_RETRYABLE", "FAILED_FINAL", "DEAD_LETTERED", "CANCELLED"]);

type Phase = "idle" | "syncing" | "done" | "error";

/**
 * "Sync now": asks the worker to fetch the latest numbers from Postiz for every post up to 30
 * days old, waits for that run to finish, then calls onSynced so the page reloads its data.
 */
export function SyncNowButton({
  onSynced,
  variant = "inline",
}: {
  onSynced?: () => void;
  variant?: "inline" | "chip";
}) {
  const { activeBrand } = useBrandContext();
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  async function start() {
    if (phase === "syncing") return;
    setPhase("syncing");
    setMessage(null);
    const clickedAt = Date.now();
    try {
      const res = await browserApiFetch<{ queued_at: string; sync: SyncStatus }>(
        `/analytics/sync?brand=${encodeURIComponent(activeBrand.brand_id)}`,
        { method: "POST" },
      );
      const since = Date.parse(res.queued_at) - 5000;
      const poll = async () => {
        if (!alive.current) return;
        try {
          const s = await browserApiFetch<SyncStatus>("/analytics/sync");
          const finished = s.last_success_at && Date.parse(s.last_success_at) >= since;
          const attempted = s.last_attempt_at && Date.parse(s.last_attempt_at) >= since;
          if (finished) {
            setPhase("done");
            onSynced?.();
            timer.current = setTimeout(() => alive.current && setPhase("idle"), 4000);
            return;
          }
          if (attempted && s.last_status && FAILED.has(s.last_status)) {
            setPhase("error");
            setMessage(`Sync failed${s.last_error_code ? ` (${s.last_error_code})` : ""}. Try again in a minute.`);
            return;
          }
        } catch {
          // keep polling through a brief network blip
        }
        if (Date.now() - clickedAt > GIVE_UP_MS) {
          setPhase("error");
          setMessage("Still syncing in the background. The numbers will update when it finishes.");
          return;
        }
        timer.current = setTimeout(() => void poll(), POLL_MS);
      };
      timer.current = setTimeout(() => void poll(), POLL_MS);
    } catch (err) {
      setPhase("error");
      setMessage(err instanceof ApiClientError ? err.message : "Couldn't start the sync.");
    }
  }

  const syncing = phase === "syncing";
  const label = syncing ? "Syncing…" : phase === "done" ? "Up to date" : variant === "chip" ? "Refresh analytics" : "Sync now";
  const Icon = phase === "done" ? Check : RefreshCw;
  const base =
    variant === "chip"
      ? "glass-chip btn-press inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-medium text-slate-100 hover:border-white/[0.22] hover:bg-white/[0.09]"
      : "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-sky-300 hover:bg-sky-400/10 hover:text-sky-200";

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={() => void start()}
        disabled={syncing}
        aria-busy={syncing}
        title="Fetch the latest likes, views, comments and shares from Instagram and Facebook"
        className={`${base} disabled:cursor-progress disabled:opacity-80`}
      >
        <Icon className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""} ${phase === "done" ? "text-emerald-300" : ""}`} aria-hidden="true" />
        {label}
      </button>
      {message && (
        <span role="status" className={`text-[11px] ${phase === "error" ? "text-amber-300/90" : "text-slate-400"}`}>
          {message}
        </span>
      )}
    </span>
  );
}
