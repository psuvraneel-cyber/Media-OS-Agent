"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Bot, CalendarClock, CheckCircle2, Loader2, Sparkles, X } from "lucide-react";

import { SegmentedControl } from "@/components/ui/Controls";
import { Dropdown } from "@/components/ui/Dropdown";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";

interface Option {
  value: string;
  label: string;
}

export interface GenerateOptions {
  can_generate: boolean;
  pillars: Option[];
  hooks: Option[];
  languages: Option[];
  generated_today: number;
  daily_limit: number;
  max_per_request: number;
}

export interface GenerationItem {
  id: string;
  status: string;
  trigger: string | null;
  created_at: string;
  content_package_id: string | null;
  qa_passed: boolean | null;
  pillar: string | null;
  error_code: string | null;
}

export interface AutopilotSettings {
  daily_drafts: number;
  auto_schedule: boolean;
  post_times: string[];
  platforms: string[];
  can_generate: boolean;
  next_daily_run: string | null;
  timezone: string;
}

const AUTO = "";
const RUNNING = new Set(["PENDING", "DISPATCHED", "RUNNING", "FAILED_RETRYABLE"]);
const COUNT_OPTIONS = [
  { value: "1", label: "1 post" },
  { value: "2", label: "2 posts" },
  { value: "3", label: "3 posts" },
] as const;
const DAILY_OPTIONS = [
  { value: "0", label: "Off" },
  { value: "1", label: "1 a day" },
  { value: "2", label: "2 a day" },
  { value: "3", label: "3 a day" },
];

function errorText(err: unknown): string {
  return err instanceof ApiClientError ? err.message : "Request failed.";
}

/** Plain-words status for one generation request. */
export function generationLabel(item: GenerationItem): { text: string; tone: "busy" | "ok" | "warn" | "bad" } {
  if (RUNNING.has(item.status)) return { text: "Writing…", tone: "busy" };
  if (item.status === "SUCCEEDED") {
    return item.qa_passed
      ? { text: "Ready for review", tone: "ok" }
      : { text: "Didn't pass the checks", tone: "warn" };
  }
  return { text: `Failed${item.error_code ? ` (${item.error_code})` : ""}`, tone: "bad" };
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function GenerateCard({ brand }: { brand: string }) {
  const { buildHrefWithBrand } = useBrandContext();
  const [opts, setOpts] = useState<GenerateOptions | null>(null);
  const [recent, setRecent] = useState<GenerationItem[]>([]);
  const [pillar, setPillar] = useState(AUTO);
  const [hook, setHook] = useState(AUTO);
  const [language, setLanguage] = useState(AUTO);
  const [count, setCount] = useState<"1" | "2" | "3">("1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = `?brand=${encodeURIComponent(brand)}`;

  const loadRecent = useCallback(() => {
    browserApiFetch<{ items: GenerationItem[] }>(`/content/generate/recent${q}`)
      .then((r) => setRecent(r.items))
      .catch(() => undefined);
  }, [q]);

  const loadOpts = useCallback(() => {
    browserApiFetch<GenerateOptions>(`/content/generate/options${q}`)
      .then(setOpts)
      .catch((err: unknown) => setError(errorText(err)));
  }, [q]);

  useEffect(() => {
    loadOpts();
    loadRecent();
  }, [loadOpts, loadRecent]);

  // Refresh while anything is still being written.
  useEffect(() => {
    if (!recent.some((r) => RUNNING.has(r.status))) return;
    const t = setInterval(loadRecent, 6000);
    return () => clearInterval(t);
  }, [recent, loadRecent]);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      await browserApiFetch(`/content/generate${q}`, {
        method: "POST",
        body: JSON.stringify({
          count: Number(count),
          pillar: pillar || null,
          hook: hook || null,
          language: language || null,
        }),
      });
      loadRecent();
      loadOpts();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (opts && !opts.can_generate) {
    return (
      <SurfaceCard icon={<Sparkles />} title="Generate posts" subtitle="Post generation isn't set up for this brand yet." >
        <p className="text-xs text-slate-400">A brand kit (voice, claims, visuals) is needed before Media OS can write for it.</p>
      </SurfaceCard>
    );
  }

  const left = opts ? Math.max(0, opts.daily_limit - opts.generated_today) : null;
  return (
    <SurfaceCard
      icon={<Sparkles />}
      title="Generate posts"
      subtitle="Media OS writes and designs carousels with free AI. They're checked automatically, then wait for your review in Approvals."
    >
      <div className="flex flex-wrap items-end gap-3 text-xs">
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-slate-500">Topic</span>
          <Dropdown
            ariaLabel="Topic"
            minWidth="11rem"
            value={pillar}
            onChange={setPillar}
            options={[{ value: AUTO, label: "Let Media OS choose" }, ...(opts?.pillars ?? [])]}
          />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-slate-500">Hook</span>
          <Dropdown
            ariaLabel="Hook"
            minWidth="10rem"
            value={hook}
            onChange={setHook}
            options={[{ value: AUTO, label: "Let Media OS choose" }, ...(opts?.hooks ?? [])]}
          />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-slate-500">Language</span>
          <Dropdown
            ariaLabel="Language"
            minWidth="10rem"
            value={language}
            onChange={setLanguage}
            options={[{ value: AUTO, label: "Let Media OS choose" }, ...(opts?.languages ?? [])]}
          />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-slate-500">How many</span>
          <SegmentedControl label="How many posts" options={COUNT_OPTIONS} value={count} onChange={setCount} size="sm" />
        </div>
        <button
          type="button"
          onClick={() => void generate()}
          disabled={busy || !opts || left === 0}
          className="btn-press inline-flex h-8 items-center gap-1.5 rounded-lg bg-gradient-to-b from-sky-300 to-sky-400 px-3.5 font-semibold text-[#04121a] disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
          Generate
        </button>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        Each post takes 2–5 minutes. You will get a Telegram message when each one is ready.
        {left !== null && ` ${left} of ${opts?.daily_limit} left today (free AI limits).`}
      </p>
      {error && <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
      {recent.length > 0 && (
        <ul className="mt-3 space-y-1.5" aria-label="Recent generation">
          {recent.slice(0, 6).map((r) => {
            const s = generationLabel(r);
            return (
              <li key={r.id} className="flex flex-wrap items-center gap-2 rounded-md bg-[var(--surface-inset)] px-3 py-1.5 text-xs">
                {s.tone === "busy" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-300" aria-hidden="true" />
                ) : s.tone === "ok" ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" aria-hidden="true" />
                ) : (
                  <X className={`h-3.5 w-3.5 ${s.tone === "warn" ? "text-amber-300" : "text-rose-300"}`} aria-hidden="true" />
                )}
                <span className="text-slate-200">{s.text}</span>
                <span className="text-slate-500">
                  {r.pillar ? r.pillar.replaceAll("_", " ") + " · " : ""}
                  {r.trigger === "daily" ? "daily draft · " : ""}
                  {fmtTime(r.created_at)}
                </span>
                {s.tone === "ok" && (
                  <Link href={buildHrefWithBrand("/approvals")} className="ml-auto text-sky-300 hover:underline">
                    Review
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </SurfaceCard>
  );
}

function AutopilotCard({ brand }: { brand: string }) {
  const [cfg, setCfg] = useState<AutopilotSettings | null>(null);
  const [draft, setDraft] = useState<AutopilotSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const path = `/brands/${encodeURIComponent(brand)}/autopilot`;

  useEffect(() => {
    browserApiFetch<AutopilotSettings>(path)
      .then((c) => {
        setCfg(c);
        setDraft(c);
      })
      .catch((err: unknown) => setError(errorText(err)));
  }, [path]);

  if (!draft || !cfg) {
    return error ? <p className="text-xs text-rose-300">{error}</p> : null;
  }
  if (!draft.can_generate) return null;

  const dirty = JSON.stringify(draft) !== JSON.stringify(cfg);
  const set = (p: Partial<AutopilotSettings>) => {
    setSaved(false);
    setDraft({ ...draft, ...p });
  };

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const next = await browserApiFetch<AutopilotSettings>(path, {
        method: "PUT",
        body: JSON.stringify({
          daily_drafts: draft.daily_drafts,
          auto_schedule: draft.auto_schedule,
          post_times: draft.post_times,
          platforms: draft.platforms,
        }),
      });
      setCfg(next);
      setDraft(next);
      setSaved(true);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SurfaceCard
      icon={<Bot />}
      title="Autopilot"
      subtitle="New drafts every morning, and approved posts placed into your posting times. Nothing goes out without your approval."
    >
      <div className="grid gap-4 text-xs md:grid-cols-2">
        <div className="space-y-1.5">
          <p className="text-[11px] font-medium text-slate-500">Daily drafts (written at 8:00 AM)</p>
          <Dropdown
            ariaLabel="Daily drafts"
            value={String(draft.daily_drafts)}
            onChange={(v) => set({ daily_drafts: Number(v) })}
            options={DAILY_OPTIONS}
          />
          {cfg.daily_drafts > 0 && cfg.next_daily_run && (
            <p className="text-[11px] text-slate-500">Next: {fmtTime(cfg.next_daily_run)}</p>
          )}
        </div>
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 text-slate-200">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 accent-sky-400"
              checked={draft.auto_schedule}
              onChange={(e) => set({ auto_schedule: e.target.checked })}
            />
            Schedule approved posts automatically
          </label>
          <div className={`flex flex-wrap items-center gap-2 ${draft.auto_schedule ? "" : "opacity-50"}`}>
            <span className="text-[11px] text-slate-500">at</span>
            {draft.post_times.map((t, i) => (
              <span key={i} className="inline-flex items-center gap-1">
                <input
                  type="time"
                  value={t}
                  disabled={!draft.auto_schedule}
                  aria-label={`Posting time ${i + 1}`}
                  onChange={(e) => set({ post_times: draft.post_times.map((x, j) => (j === i ? e.target.value : x)) })}
                  className="h-7 rounded-md border border-white/[0.08] bg-[var(--surface-inset)] px-2 text-slate-100 [color-scheme:dark] focus:border-sky-400/60 focus:outline-none"
                />
                {draft.post_times.length > 1 && (
                  <button
                    type="button"
                    aria-label={`Remove posting time ${i + 1}`}
                    disabled={!draft.auto_schedule}
                    onClick={() => set({ post_times: draft.post_times.filter((_, j) => j !== i) })}
                    className="rounded p-0.5 text-slate-500 hover:text-rose-300"
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                )}
              </span>
            ))}
            {draft.post_times.length < 3 && (
              <button
                type="button"
                disabled={!draft.auto_schedule}
                onClick={() => set({ post_times: [...draft.post_times, "12:00"] })}
                className="rounded-md px-2 py-1 text-[11px] text-sky-300 hover:bg-white/[0.05]"
              >
                + time
              </button>
            )}
          </div>
          <p className="text-[11px] text-slate-500">
            Each approved Media OS carousel takes the next free time (your daily cap and 5-minute spacing still apply). Your own uploads are scheduled by hand.
          </p>
        </div>
      </div>
      {error && <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || busy}
          className="btn-press inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-b from-sky-300 to-sky-400 px-3 py-1.5 text-xs font-semibold text-[#04121a] disabled:opacity-40"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" />}
          Save autopilot
        </button>
        {saved && !dirty && <span className="text-[11px] text-emerald-300">Saved</span>}
      </div>
    </SurfaceCard>
  );
}

/** "Create with AI" section of Studio: on-demand generation plus the autopilot settings. */
export function AutopilotPanel({ brand }: { brand: string }) {
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <GenerateCard brand={brand} />
      <AutopilotCard brand={brand} />
    </div>
  );
}
