"use client";

import React from "react";
import { Clock3, ExternalLink, ImageOff, RefreshCw } from "lucide-react";

import { AreaChart, BarList } from "@/components/charts/AreaChart";
import { Badge } from "@/components/ui/Badge";
import { AnimatedNumber, DeltaPill, InfoTip, SegmentedControl } from "@/components/ui/Controls";
import { Skeleton } from "@/components/ui/FeedbackStates";
import { RANGE_LABEL, RANGE_OPTIONS, type RangeKey, type SyncStatus, type ViewsReport } from "@/lib/analytics";
import { assetUrl } from "@/lib/analytics-paths";
import {
  PLATFORM_LABEL,
  formatBucket,
  formatCompact,
  formatDate,
  formatDateTime,
  formatInt,
  relativeTime,
} from "@/lib/format";

export const VIEWS_TOOLTIP =
  "Platform-reported views: each platform's own native view count (Instagram 'views'; Facebook " +
  "video views). Reach, unique viewers, impressions and plays are never added in. Views are " +
  "counted when they were received, so older posts still contribute to recent periods.";

export const PLATFORM_COLOR: Record<string, string> = {
  INSTAGRAM: "var(--platform-instagram)",
  FACEBOOK: "var(--platform-facebook)",
  YOUTUBE: "var(--platform-youtube)",
};

export function PlatformMark({ platform }: { platform: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ background: PLATFORM_COLOR[platform] ?? "var(--chart-axis)" }}
        aria-hidden="true"
      />
      <span>{PLATFORM_LABEL[platform] ?? platform}</span>
    </span>
  );
}

export function RangeControl({
  value,
  onChange,
  size = "md",
}: {
  value: RangeKey;
  onChange: (r: RangeKey) => void;
  size?: "sm" | "md";
}) {
  return (
    <SegmentedControl label="Reporting period" options={RANGE_OPTIONS} value={value} onChange={onChange} size={size} />
  );
}

export function SyncLine({ sync, report }: { sync: SyncStatus; report?: ViewsReport | null }) {
  const failed = sync.last_status && !["SUCCEEDED", "PENDING", "RUNNING", "DISPATCHED"].includes(sync.last_status);
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
      <span className="inline-flex items-center gap-1">
        <RefreshCw className="h-3 w-3" aria-hidden="true" />
        Last analytics sync:{" "}
        <span className="text-slate-300" title={sync.last_success_at ?? undefined}>
          {sync.last_success_at ? relativeTime(sync.last_success_at) : "never"}
        </span>
      </span>
      {!sync.schedule_enabled && (
        <span className="text-amber-300/90">Automatic sync is off</span>
      )}
      {failed && (
        <span className="text-rose-300/90">
          Last attempt {sync.last_status?.toLowerCase().replace("_", " ")}
          {sync.last_error_code ? ` (${sync.last_error_code})` : ""}
        </span>
      )}
      {report?.coverage.tracking_since && (
        <span className="inline-flex items-center gap-1">
          <Clock3 className="h-3 w-3" aria-hidden="true" />
          Tracked since {formatDate(report.coverage.tracking_since)}
        </span>
      )}
    </p>
  );
}

export function PeriodLabel({ report }: { report: ViewsReport }) {
  return (
    <span className="text-[11px] text-slate-500">
      {RANGE_LABEL[report.range]} · {formatDate(report.window.start)} – {formatDate(report.window.end)} (IST)
    </span>
  );
}

/** Large views KPI with growth, period and range selector. */
export function ViewsHero({
  report,
  loading,
  range,
  onRange,
}: {
  report: ViewsReport | null;
  loading: boolean;
  range: RangeKey;
  onRange: (r: RangeKey) => void;
}) {
  const t = report?.totals;
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <p className="flex items-center gap-1.5 text-xs font-medium text-slate-400">
          Total views
          <InfoTip text={VIEWS_TOOLTIP} label="How total views are counted" />
          {t?.approximate && (
            <span className="text-[11px] font-normal text-slate-500" title="Some views were split across a period boundary.">
              ≈ estimated at period edges
            </span>
          )}
        </p>
        <div className="mt-1.5 flex flex-wrap items-baseline gap-x-4 gap-y-1">
          {report && t ? (
            <AnimatedNumber
              value={t.views ?? 0}
              format={formatInt}
              className="text-[2.5rem] leading-none font-semibold tracking-tight text-slate-50"
            />
          ) : (
            <Skeleton className="h-10 w-40" />
          )}
          {report && t && <DeltaPill pct={report.range === "all" ? null : t.views_growth_pct} />}
        </div>
        <div className="mt-2">{report ? <PeriodLabel report={report} /> : <Skeleton className="h-3 w-56" />}</div>
      </div>
      <div className={`transition-opacity ${loading ? "opacity-70" : ""}`}>
        <RangeControl value={range} onChange={onRange} />
      </div>
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tip,
  format = formatInt,
  emphasis = false,
}: {
  label: string;
  value: number | null | undefined;
  hint?: React.ReactNode;
  tip?: string;
  format?: (n: number) => string;
  emphasis?: boolean;
}) {
  return (
    <div className="min-w-0">
      <p className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
        {label}
        {tip && <InfoTip text={tip} label={`About ${label}`} />}
      </p>
      <p className={`mt-1 font-semibold tracking-tight text-slate-100 ${emphasis ? "text-2xl" : "text-lg"}`}>
        {value === null || value === undefined ? (
          <span className="text-slate-500">—</span>
        ) : (
          <AnimatedNumber value={value} format={format} />
        )}
      </p>
      {hint && <div className="mt-0.5 text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

export function ViewsTrendChart({ report, height = 220 }: { report: ViewsReport; height?: number }) {
  const points = report.trend.map((p) => ({
    key: p.date,
    label: formatBucket(p.date, report.granularity),
    value: p.views,
  }));
  return (
    <AreaChart
      points={points}
      height={height}
      formatValue={formatCompact}
      ariaLabel={`Views per ${report.granularity}, ${RANGE_LABEL[report.range]}`}
    />
  );
}

export function PlatformBreakdown({ report }: { report: ViewsReport }) {
  return (
    <BarList
      formatValue={formatInt}
      emptyLabel="No published posts on any platform yet"
      items={report.by_platform.map((p) => ({
        key: p.platform,
        label: (
          <span className="inline-flex items-center gap-1.5">
            <PlatformMark platform={p.platform} />
            <InfoTip text={p.view_definition} label={`${PLATFORM_LABEL[p.platform] ?? p.platform} view definition`} />
          </span>
        ),
        value: p.posts_with_views > 0 ? p.views : null,
        hint:
          p.posts_with_views === 0 && p.unique_viewers !== null
            ? `${formatInt(p.unique_viewers)} unique viewers`
            : `${p.published_posts} post${p.published_posts === 1 ? "" : "s"}`,
        color: PLATFORM_COLOR[p.platform],
      }))}
    />
  );
}

export function AccountBreakdown({ report }: { report: ViewsReport }) {
  return (
    <BarList
      formatValue={formatInt}
      emptyLabel="No connected account has published posts yet"
      items={report.by_account.map((a) => ({
        key: a.channel_account_id,
        label: (
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ background: PLATFORM_COLOR[a.platform] }}
              aria-hidden="true"
            />
            <span className="truncate">{a.name}</span>
            <span className="text-slate-500">· {PLATFORM_LABEL[a.platform] ?? a.platform}</span>
          </span>
        ),
        value: a.posts_with_views > 0 ? a.views : null,
        hint:
          a.posts_with_views === 0 && a.unique_viewers !== null
            ? `${formatInt(a.unique_viewers)} unique viewers`
            : `${a.published_posts} post${a.published_posts === 1 ? "" : "s"}`,
        color: PLATFORM_COLOR[a.platform],
      }))}
    />
  );
}

export function PostThumb({
  brandId,
  assetId,
  size = 40,
}: {
  brandId: string;
  assetId: string | null | undefined;
  size?: number;
}) {
  const style = { width: size, height: Math.round(size * 1.25) };
  if (!assetId) {
    return (
      <span
        className="flex shrink-0 items-center justify-center rounded-md border border-white/[0.06] bg-white/[0.03] text-slate-600"
        style={style}
      >
        <ImageOff className="h-3.5 w-3.5" aria-hidden="true" />
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin asset proxy
    <img
      src={assetUrl(brandId, assetId)}
      alt=""
      loading="lazy"
      className="shrink-0 rounded-md border border-white/[0.06] object-cover"
      style={style}
    />
  );
}

export function TopPostCard({
  report,
  brandId,
  onOpen,
}: {
  report: ViewsReport;
  brandId: string;
  onOpen?: (packageId: string) => void;
}) {
  const p = report.top_post;
  if (!p) {
    return (
      <p className="text-xs leading-relaxed text-slate-500">
        No post received measured views in this period.
      </p>
    );
  }
  const body = (
    <div className="flex items-start gap-3 text-left">
      <PostThumb brandId={brandId} assetId={p.thumbnail_asset_id} size={44} />
      <div className="min-w-0">
        <p className="bn line-clamp-2 text-[13px] font-medium text-slate-100" lang="bn">
          {p.title}
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] text-slate-500">
          <PlatformMark platform={p.platform} />
          <span>{formatDate(p.published_at)}</span>
        </p>
        <p className="mt-1.5 text-sm font-semibold text-slate-50 tabular">
          {formatInt(p.views_in_period)}
          <span className="ml-1 text-[11px] font-normal text-slate-500">views this period</span>
        </p>
      </div>
    </div>
  );
  return onOpen ? (
    <button
      type="button"
      onClick={() => onOpen(p.content_package_id)}
      className="w-full rounded-lg p-1 -m-1 transition-colors hover:bg-white/[0.03]"
    >
      {body}
    </button>
  ) : (
    body
  );
}

export function PostLink({ url }: { url: string | null }) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-[11px] text-sky-300 hover:text-sky-200"
    >
      View on platform <ExternalLink className="h-3 w-3" aria-hidden="true" />
    </a>
  );
}

export function NoAnalyticsYet({
  report,
  brandName,
}: {
  report: ViewsReport;
  brandName: string;
}) {
  const t = report.totals;
  if (t.published_posts === 0) {
    return (
      <p className="text-xs leading-relaxed text-slate-400">
        No posts have been published for {brandName} yet. Views appear here after the first post goes
        live and the analytics sync reads it from the platform.
      </p>
    );
  }
  if (t.posts_with_view_data === 0) {
    return (
      <p className="text-xs leading-relaxed text-slate-400">
        {t.published_posts} published post{t.published_posts === 1 ? "" : "s"}, but no platform metrics have
        been collected yet. {report.sync.schedule_enabled ? "They arrive with the next analytics sync." : "Enable the analytics sync schedule to start collecting them."}
      </p>
    );
  }
  return null;
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "PUBLISHED" ? "live" : status === "FAILED" ? "blocked" : status === "SCHEDULED" ? "info" : "disabled";
  return <Badge tone={tone}>{status.charAt(0) + status.slice(1).toLowerCase()}</Badge>;
}

export { formatDateTime };
