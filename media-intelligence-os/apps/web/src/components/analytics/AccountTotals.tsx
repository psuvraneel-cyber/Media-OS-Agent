"use client";

import React from "react";
import { Globe2 } from "lucide-react";

import { PlatformMark } from "@/components/analytics/ViewsPanels";
import { AnimatedNumber, InfoTip, SegmentedControl } from "@/components/ui/Controls";
import { Skeleton } from "@/components/ui/FeedbackStates";
import { formatDate, formatInt } from "@/lib/format";

export interface AccountChannel {
  channel_id: string;
  platform: string;
  name: string;
  observed_on: string;
  observed_at: string;
  metrics: Record<string, number>;
  display: { key: string; label: string; value: number | null }[];
}

export interface AccountReport {
  window_days: 7 | 30;
  channels: AccountChannel[];
  combined: Record<string, number | null>;
}

export type AccountWindow = "7" | "30";

const WINDOW_OPTIONS: ReadonlyArray<{ value: AccountWindow; label: string }> = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
];

export const ACCOUNT_TOOLTIP =
  "Read once a day from each connected account through Postiz. These totals cover every post on " +
  "the account, including ones you post directly from the app, so they can differ from the " +
  "per-post numbers above. Instagram's account numbers can run up to 2 days behind.";

/** Whole-account totals per platform, with a 7 / 30 day switch. */
export function AccountTotalsCard({
  report,
  loading,
  window,
  onWindow,
}: {
  report: AccountReport | null;
  loading: boolean;
  window: AccountWindow;
  onWindow: (w: AccountWindow) => void;
}) {
  const channels = report?.channels ?? [];
  const asOf = channels.map((c) => c.observed_on).sort().at(-1);
  return (
    <section aria-label="Whole account" className="surface p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-1.5 text-sm font-semibold text-slate-100">
            <Globe2 className="h-4 w-4 text-sky-300" aria-hidden="true" />
            Whole account
            <InfoTip text={ACCOUNT_TOOLTIP} label="What whole-account totals include" />
          </p>
          <p className="mt-0.5 text-xs text-slate-400">
            Every post on the account, including ones posted directly from Instagram or Facebook.
          </p>
        </div>
        <SegmentedControl label="Account window" options={WINDOW_OPTIONS} value={window} onChange={onWindow} size="sm" />
      </div>

      {loading && !report ? (
        <div className="grid gap-3 md:grid-cols-2">
          <Skeleton className="h-24 rounded-xl" />
          <Skeleton className="h-24 rounded-xl" />
        </div>
      ) : channels.length === 0 ? (
        <p className="rounded-lg border border-dashed border-white/[0.08] px-4 py-5 text-center text-xs text-slate-400">
          No account totals yet. They are read every evening at 9:35 PM IST, or press Refresh analytics to read them now.
        </p>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {channels.map((ch) => (
            <div key={ch.channel_id} className="rounded-xl border border-white/[0.06] bg-[var(--surface-inset)] p-4">
              <div className="mb-3 flex items-center justify-between gap-2 text-xs">
                <span className="font-medium text-slate-200">
                  <PlatformMark platform={ch.platform} />
                </span>
                <span className="truncate text-slate-500">{ch.name}</span>
              </div>
              <dl className="grid grid-cols-3 gap-x-3 gap-y-3">
                {ch.display.map((d) => (
                  <div key={d.key} className="min-w-0">
                    <dt className="truncate text-[11px] text-slate-500">{d.label}</dt>
                    <dd className="text-lg font-semibold text-slate-100 tabular">
                      {d.value === null ? <span className="text-slate-600">—</span> : <AnimatedNumber value={d.value} format={formatInt} />}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      )}
      {asOf && (
        <p className="mt-3 text-[11px] text-slate-500">
          Last {report?.window_days} days · read {formatDate(asOf)}
          {channels.some((c) => c.platform === "INSTAGRAM") ? " · Instagram can run up to 2 days behind" : ""}
        </p>
      )}
    </section>
  );
}
