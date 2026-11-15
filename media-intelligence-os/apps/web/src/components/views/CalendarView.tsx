"use client";

import React, { useMemo, useState } from "react";
import {
  AlertTriangle,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
} from "lucide-react";

import { PLATFORM_COLOR, PostThumb } from "@/components/analytics/ViewsPanels";
import { ErrorBanner, Skeleton } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { useApiResource } from "@/lib/analytics";
import { buildQuery } from "@/lib/analytics-paths";
import { useBrandContext } from "@/lib/brand-context";
import { PLATFORM_LABEL } from "@/lib/format";

const TZ = "Asia/Kolkata";
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

interface CalendarEntry {
  schedule_id: string;
  content_package_id: string;
  title: string;
  platform: string;
  status: string;
  overdue: boolean;
  scheduled_at: string;
  post_url: string | null;
  thumbnail_asset_id: string | null;
}

interface CalendarResponse {
  start: string;
  end: string;
  items: CalendarEntry[];
}

/** Calendar maths in IST: `YYYY-MM-DD` keys, midnight IST as the boundary. */
function istKey(d: Date): string {
  return d.toLocaleDateString("en-CA", { timeZone: TZ });
}

function istMidnight(key: string): Date {
  return new Date(`${key}T00:00:00+05:30`);
}

function addDays(key: string, days: number): string {
  const d = istMidnight(key);
  d.setUTCDate(d.getUTCDate() + days);
  return istKey(d);
}

function monthGrid(year: number, month: number): string[] {
  const first = `${year}-${String(month + 1).padStart(2, "0")}-01`;
  // Noon IST is 06:30 UTC on the same date, so getUTCDay() is the IST weekday. Monday = 0.
  const weekday = (new Date(`${first}T12:00:00+05:30`).getUTCDay() + 6) % 7;
  const start = addDays(first, -weekday);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

function statusClasses(e: CalendarEntry): string {
  if (e.overdue) return "border-amber-400/30 bg-amber-400/[0.08] text-amber-200";
  if (e.status === "PUBLISHED") return "border-emerald-400/20 bg-emerald-400/[0.06] text-slate-200";
  if (e.status === "FAILED") return "border-rose-400/30 bg-rose-400/[0.08] text-rose-200";
  if (e.status === "CANCELLED") return "border-white/[0.06] bg-transparent text-slate-500 line-through";
  return "border-sky-400/20 bg-sky-400/[0.06] text-slate-200";
}

function statusLabel(e: CalendarEntry): string {
  if (e.overdue) return "Overdue";
  return e.status.charAt(0) + e.status.slice(1).toLowerCase();
}

function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
}

function EntryChip({ e, brandId, compact }: { e: CalendarEntry; brandId: string; compact: boolean }) {
  const body = (
    <span className="flex min-w-0 items-center gap-1.5">
      <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: PLATFORM_COLOR[e.platform] }} aria-hidden="true" />
      <span className="shrink-0 tabular text-[10px] opacity-80">{timeLabel(e.scheduled_at)}</span>
      <span className="bn truncate" lang="bn">
        {e.title}
      </span>
    </span>
  );
  const title = `${e.title} · ${PLATFORM_LABEL[e.platform] ?? e.platform} · ${statusLabel(e)}`;
  const cls = `cal-chip block w-full rounded-md border px-1.5 py-1 text-left text-[11px] ${statusClasses(e)}`;
  if (compact) {
    return e.post_url ? (
      <a href={e.post_url} target="_blank" rel="noopener noreferrer" title={title} className={`${cls} hover:brightness-125`}>
        {body}
      </a>
    ) : (
      <span title={title} className={cls}>
        {body}
      </span>
    );
  }
  return (
    <div className={`cal-chip flex items-center gap-3 rounded-lg border px-3 py-2 ${statusClasses(e)}`}>
      <PostThumb brandId={brandId} assetId={e.thumbnail_asset_id} size={32} />
      <div className="min-w-0 flex-1">
        <p className="bn truncate text-[13px]" lang="bn">
          {e.title}
        </p>
        <p className="mt-0.5 text-[11px] opacity-80">
          {timeLabel(e.scheduled_at)} IST · {PLATFORM_LABEL[e.platform] ?? e.platform} · {statusLabel(e)}
        </p>
      </div>
      {e.post_url && (
        <a href={e.post_url} target="_blank" rel="noopener noreferrer" aria-label="View post on platform" className="text-sky-300 hover:text-sky-200">
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      )}
    </div>
  );
}

export function CalendarView({ postizUrl = "http://localhost:4007" }: { postizUrl?: string }) {
  const { activeBrand } = useBrandContext();
  const brandId = activeBrand.brand_id;
  const todayKey = istKey(new Date());
  const [cursor, setCursor] = useState(() => {
    const [y, m] = todayKey.split("-").map(Number);
    return { year: y, month: m - 1 };
  });
  const days = useMemo(() => monthGrid(cursor.year, cursor.month), [cursor]);
  const start = istMidnight(days[0]).toISOString();
  const end = istMidnight(addDays(days[days.length - 1], 1)).toISOString();
  const res = useApiResource<CalendarResponse>(
    `/content/schedules?${buildQuery({ brand: brandId, start, end })}`,
  );
  const items = useMemo(() => res.data?.items ?? [], [res.data]);
  const byDay = useMemo(() => {
    const map = new Map<string, CalendarEntry[]>();
    for (const e of items) {
      const key = istKey(new Date(e.scheduled_at));
      map.set(key, [...(map.get(key) ?? []), e]);
    }
    return map;
  }, [items]);
  const overdue = items.filter((e) => e.overdue);
  const monthLabel = new Date(`${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}-15T12:00:00+05:30`).toLocaleDateString("en-IN", {
    timeZone: TZ,
    month: "long",
    year: "numeric",
  });
  const monthPrefix = `${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}`;
  const agendaDays = days.filter((d) => d.startsWith(monthPrefix) && byDay.has(d));

  function shift(delta: number) {
    setCursor((c) => {
      const m = c.month + delta;
      return { year: c.year + Math.floor(m / 12), month: ((m % 12) + 12) % 12 };
    });
  }

  return (
    <div className="space-y-6">
      {overdue.length > 0 && (
        <div role="alert" className="flex items-start gap-3 rounded-xl border border-amber-400/25 bg-amber-400/[0.06] p-4 text-sm text-amber-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
          <div className="text-xs leading-relaxed">
            <p className="font-medium text-amber-100">
              {overdue.length} post{overdue.length === 1 ? " is" : "s are"} overdue
            </p>
            <p className="mt-0.5 text-amber-200/80">
              Still not published 30 minutes after the scheduled time. Usually the PC or Docker was off at
              post time. Overdue posts no longer count toward the daily cap. Check them in Postiz: it may
              still publish them, or delete them there to reschedule from Approvals.
            </p>
          </div>
        </div>
      )}

      <SurfaceCard
        icon={<CalendarClock />} title={`Publishing calendar — ${activeBrand.name}`}
        subtitle="Every scheduled and published platform post, in IST. Schedule new posts from Approvals."
        badge={
          <a
            href={postizUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-[var(--surface-inset)] px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:border-white/[0.16]"
          >
            Open Postiz <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </a>
        }
      >
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-1">
            <button type="button" onClick={() => shift(-1)} aria-label="Previous month" className="btn-press rounded-md border border-white/[0.08] p-1.5 text-slate-300 hover:border-sky-400/30 hover:bg-sky-400/10 hover:text-sky-200">
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button type="button" onClick={() => shift(1)} aria-label="Next month" className="btn-press rounded-md border border-white/[0.08] p-1.5 text-slate-300 hover:border-sky-400/30 hover:bg-sky-400/10 hover:text-sky-200">
              <ChevronRight className="h-4 w-4" />
            </button>
            <h3 key={monthLabel} className="animate-fade-up ml-2 text-base font-semibold tracking-tight text-slate-50" aria-live="polite">
              {monthLabel}
            </h3>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
            <button
              type="button"
              onClick={() => {
                const [y, m] = todayKey.split("-").map(Number);
                setCursor({ year: y, month: m - 1 });
              }}
              className="rounded-md px-2 py-1 text-slate-300 transition-colors hover:bg-white/[0.05]"
            >
              Today
            </button>
            {[
              ["Scheduled", "bg-sky-400"],
              ["Published", "bg-emerald-400"],
              ["Overdue", "bg-amber-400"],
              ["Failed", "bg-rose-400"],
            ].map(([label, color]) => (
              <span key={label} className="inline-flex items-center gap-1">
                <span className={`h-2 w-2 rounded-sm ${color}`} aria-hidden="true" />
                {label}
              </span>
            ))}
          </div>
        </div>

        {res.error && !res.data && <ErrorBanner title="Calendar unavailable" message={res.error} />}

        {/* Month grid (md and up) */}
        <div className={`hidden md:block ${res.loading ? "opacity-60 transition-opacity" : "transition-opacity"}`}>
          <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border border-white/[0.06] bg-white/[0.06]">
            {WEEKDAYS.map((d) => (
              <div key={d} className="bg-[var(--glass-tint)] px-2 py-2 text-[10px] font-semibold tracking-[0.1em] text-slate-500 uppercase">
                {d}
              </div>
            ))}
            {days.map((key) => {
              const inMonth = key.startsWith(monthPrefix);
              const entries = byDay.get(key) ?? [];
              const isToday = key === todayKey;
              return (
                <div
                  key={key}
                  className={`cal-day min-h-[6.5rem] bg-[var(--glass-tint)] p-1.5 ${inMonth ? "" : "opacity-45"} ${isToday ? "cal-day--today" : ""}`}
                >
                  <p
                    className={`mb-1 text-[11px] tabular ${
                      isToday
                        ? "cal-today-dot inline-flex h-5 w-5 items-center justify-center rounded-full bg-gradient-to-b from-sky-300 to-sky-500 font-semibold text-[#04121a]"
                        : entries.length > 0
                          ? "font-medium text-slate-300"
                          : "text-slate-500"
                    }`}
                  >
                    {Number(key.slice(8))}
                  </p>
                  <div className="space-y-1">
                    {entries.slice(0, 3).map((e) => (
                      <EntryChip key={e.schedule_id} e={e} brandId={brandId} compact />
                    ))}
                    {entries.length > 3 && <p className="px-1 text-[10px] text-slate-500">+{entries.length - 3} more</p>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Agenda (phones) */}
        <div className="md:hidden">
          {!res.data ? (
            <Skeleton className="h-32" />
          ) : agendaDays.length === 0 ? (
            <p className="py-8 text-center text-xs text-slate-500">Nothing scheduled or published this month.</p>
          ) : (
            <ol className="space-y-4">
              {agendaDays.map((key) => (
                <li key={key}>
                  <p className="mb-1.5 text-[11px] font-medium text-slate-400">
                    {istMidnight(key).toLocaleDateString("en-IN", { timeZone: TZ, weekday: "short", day: "numeric", month: "short" })}
                  </p>
                  <div className="space-y-1.5">
                    {(byDay.get(key) ?? []).map((e) => (
                      <EntryChip key={e.schedule_id} e={e} brandId={brandId} compact={false} />
                    ))}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>

        {res.data && items.length === 0 && (
          <p className="mt-4 hidden text-center text-xs text-slate-500 md:block">Nothing scheduled or published in this view.</p>
        )}
      </SurfaceCard>
    </div>
  );
}
