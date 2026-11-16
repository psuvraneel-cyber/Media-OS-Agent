"use client";

import React, { useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  Database,
  FileText,
  LineChart,
  Plug,
  Radar,
  RefreshCw,
  Rocket,
  Send,
  Server,
  ShieldCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import {
  AccountBreakdown,
  NoAnalyticsYet,
  PlatformBreakdown,
  StatTile,
  SyncLine,
  TopPostCard,
  ViewsHero,
  ViewsTrendChart,
} from "@/components/analytics/ViewsPanels";
import { BrandLogoEditor } from "@/components/shell/BrandLogoEditor";
import { PostizRestartButton } from "@/components/shell/PostizRestartButton";
import { Badge, StatusDot, type BadgeTone } from "@/components/ui/Badge";
import { ErrorBanner, Skeleton } from "@/components/ui/FeedbackStates";
import { SectionHeader, SurfaceCard } from "@/components/ui/SurfaceCard";
import {
  useApiResource,
  type Initial,
  type PipelineSummary,
  type RangeKey,
  type ViewsReport,
} from "@/lib/analytics";
import { DEFAULT_RANGE, pipelinePath, viewsPath } from "@/lib/analytics-paths";
import { useBrandContext } from "@/lib/brand-context";
import { PLATFORM_LABEL, formatDateTime, formatInt, humanizeEnum, relativeTime } from "@/lib/format";
import type { SystemStatusResponse } from "@/lib/types";
import { useNow } from "@/lib/use-now";

interface DashboardOverviewViewProps {
  systemStatus: SystemStatusResponse | null;
  errorMessage?: string;
  initialViews?: Initial<ViewsReport>;
  initialPipeline?: Initial<PipelineSummary>;
}

interface HealthItem {
  label: string;
  value: string;
  detail: string;
  tone: BadgeTone;
  /** Small control shown at the top right of the tile (e.g. Restart Postiz). */
  action?: React.ReactNode;
}

const HEALTH_ICONS: Record<string, LucideIcon> = {
  "Control plane": Server,
  Storage: Database,
  "Postiz gateway": Send,
  "Research ingestion": Radar,
  "Analytics sync": RefreshCw,
};

const TONE_ICON_CLASS: Record<BadgeTone, string> = {
  live: "text-emerald-300 border-emerald-400/20 bg-emerald-400/[0.08] shadow-[0_0_16px_-4px_rgba(45,212,191,0.5)]",
  blocked: "text-rose-300 border-rose-400/20 bg-rose-400/[0.08] shadow-[0_0_16px_-4px_rgba(251,113,133,0.5)]",
  prototype: "text-amber-300 border-amber-400/20 bg-amber-400/[0.08]",
  info: "text-sky-300 border-sky-400/20 bg-sky-400/[0.08]",
  disabled: "text-slate-500 border-white/[0.06] bg-white/[0.02]",
  neutral: "text-slate-400 border-white/[0.06] bg-white/[0.02]",
};

/* ------------------------------------------------------------------ next post countdown */

const DAY_MS = 24 * 60 * 60 * 1000;

function splitDuration(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  return {
    d: Math.floor(total / 86400),
    h: Math.floor((total % 86400) / 3600),
    m: Math.floor((total % 3600) / 60),
    s: total % 60,
  };
}

function TimeBlock({ value, unit }: { value: number; unit: string }) {
  const text = String(value).padStart(2, "0");
  return (
    <span className="flex flex-col items-center">
      <span className="relative flex h-12 min-w-[3.1rem] items-center justify-center overflow-hidden rounded-lg border border-white/[0.08] bg-gradient-to-b from-white/[0.06] to-white/[0.01] px-2 font-mono text-2xl font-semibold text-slate-50 tabular shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_8px_20px_-12px_rgba(0,0,0,0.8)]">
        {/* re-key per value so each change rolls in */}
        <span key={text} className="tick-in">
          {text}
        </span>
        <span aria-hidden="true" className="absolute inset-x-0 top-1/2 h-px bg-black/30" />
      </span>
      <span className="mt-1 text-[10px] tracking-[0.12em] text-slate-500 uppercase">{unit}</span>
    </span>
  );
}

function NextPostCountdown({
  pipeline,
  buildHref,
}: {
  pipeline: PipelineSummary | null;
  buildHref: (p: string) => string;
}) {
  const now = useNow();
  const at = pipeline?.publishing.next_scheduled_at ?? null;
  const platform = pipeline?.publishing.next_scheduled_platform ?? null;

  if (!pipeline) {
    return <Skeleton className="h-[7.5rem] rounded-xl" />;
  }

  if (!at) {
    return (
      <section
        aria-label="Next scheduled post"
        className="surface flex flex-wrap items-center justify-between gap-4 px-5 py-4"
      >
        <div className="flex items-center gap-3.5">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.03] text-slate-400">
            <CalendarClock className="h-5 w-5" aria-hidden="true" />
          </span>
          <div>
            <p className="text-sm font-semibold text-slate-100">No post in the queue</p>
            <p className="mt-0.5 text-xs text-slate-500">Approve and schedule a carousel to see a live countdown here.</p>
          </div>
        </div>
        <Link
          href={buildHref("/approvals")}
          className="btn-press inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-400/10"
        >
          Go to approvals <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      </section>
    );
  }

  const target = new Date(at).getTime();
  const remaining = now === null ? null : target - now;
  const live = remaining !== null && remaining <= 0;
  const parts = splitDuration(remaining ?? 0);
  // Ring shows how much of the final 24 h is left (full when further out than a day).
  const frac = remaining === null ? 1 : Math.min(1, Math.max(0, remaining / DAY_MS));
  const R = 30;
  const C = 2 * Math.PI * R;

  return (
    <section
      aria-label="Next scheduled post"
      className="surface glow-border overflow-hidden px-5 py-5"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-24 -right-16 h-56 w-72 rounded-full bg-[radial-gradient(closest-side,rgba(34,211,238,0.14),transparent)]"
      />
      <div className="relative flex flex-wrap items-center justify-between gap-5">
        <div className="flex min-w-0 items-center gap-4">
          <div className="relative h-[76px] w-[76px] shrink-0">
            <svg viewBox="0 0 76 76" className="h-full w-full -rotate-90" aria-hidden="true">
              <defs>
                <linearGradient id="countdown-ring" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" style={{ stopColor: "var(--accent-strong)" }} />
                  <stop offset="100%" style={{ stopColor: "var(--chart-2)" }} />
                </linearGradient>
              </defs>
              <circle cx="38" cy="38" r={R} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="5" />
              <circle
                cx="38"
                cy="38"
                r={R}
                fill="none"
                stroke="url(#countdown-ring)"
                strokeWidth="5"
                strokeLinecap="round"
                strokeDasharray={C}
                strokeDashoffset={C * (1 - frac)}
                className="ring-progress"
                style={{ filter: "drop-shadow(0 0 6px rgba(34,211,238,0.6))" }}
              />
            </svg>
            <span className="absolute inset-0 flex items-center justify-center text-sky-200">
              <Rocket className={`h-6 w-6 ${live ? "animate-bounce" : ""}`} aria-hidden="true" />
            </span>
          </div>
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-[11px] font-medium tracking-[0.1em] text-sky-300/90 uppercase">
              <StatusDot tone="info" pulse />
              {live ? "Going live now" : "Next post goes live in"}
            </p>
            <p className="mt-1 truncate text-[13px] text-slate-300">
              {formatDateTime(at)} IST
              {platform && <span className="text-slate-500"> · {PLATFORM_LABEL[platform] ?? platform}</span>}
            </p>
            {live && (
              <p className="mt-1 text-xs text-slate-500">Waiting for the platform to confirm the post.</p>
            )}
          </div>
        </div>

        {!live && (
          <div className="flex items-start gap-1.5" role="timer" aria-live="off" aria-label={`Time until next post: ${relativeTime(at)}`}>
            {now === null ? (
              <Skeleton className="h-12 w-56" />
            ) : (
              <>
                {parts.d > 0 && <TimeBlock value={parts.d} unit="days" />}
                <TimeBlock value={parts.h} unit="hrs" />
                <span className="pt-2.5 font-mono text-xl text-slate-600">:</span>
                <TimeBlock value={parts.m} unit="min" />
                <span className="pt-2.5 font-mono text-xl text-slate-600">:</span>
                <TimeBlock value={parts.s} unit="sec" />
              </>
            )}
          </div>
        )}

        <Link
          href={buildHref("/calendar")}
          className="btn-press inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-400/10"
        >
          Open calendar <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      </div>
    </section>
  );
}

function healthItems(
  status: SystemStatusResponse | null,
  pipeline: PipelineSummary | null,
  views: ViewsReport | null,
): HealthItem[] {
  const storage = status
    ? status.persistent_storage
      ? "PostgreSQL + Redis"
      : status.database_status === "unreachable"
        ? "DB Unreachable"
        : "Not Configured"
    : "Unknown";
  const research = pipeline?.research ?? [];
  const live = research.filter((r) => r.mode !== "OFF" && r.mode !== "BLOCKED");
  const sync = views?.sync;
  return [
    {
      label: "Control plane",
      value: status ? "Online" : "Degraded",
      detail: status ? `API · ${status.phase}` : "Status unavailable",
      tone: status ? "live" : "blocked",
    },
    {
      label: "Storage",
      value: storage,
      detail: status?.persistent_storage
        ? `Schema ${status.schema_version ?? "head"} · Redis ${status.redis_status}`
        : "Health unavailable",
      tone: status?.persistent_storage ? "live" : "blocked",
    },
    {
      label: "Postiz gateway",
      value: status ? humanizeEnum(status.postiz.status) : "Unknown",
      detail: status?.postiz.detail ?? "Probe unavailable",
      tone:
        status?.postiz.status === "reachable"
          ? "live"
          : status?.postiz.status === "unreachable"
            ? "blocked"
            : "disabled",
      action: status ? <PostizRestartButton /> : undefined,
    },
    {
      label: "Research ingestion",
      value: pipeline ? (live.length ? `${live.length} source${live.length > 1 ? "s" : ""} on` : "Paused") : "—",
      detail: pipeline
        ? `${research.length} registered · ${research.filter((r) => r.mode === "BLOCKED").length} blocked by policy`
        : "Loading…",
      tone: pipeline ? (live.length ? "live" : "disabled") : "disabled",
    },
    {
      label: "Analytics sync",
      value: sync ? (sync.last_success_at ? relativeTime(sync.last_success_at) : "No sync yet") : "—",
      detail: sync ? (sync.schedule_enabled ? "Every 6 h via Postiz" : "Schedule off") : "Loading…",
      tone: sync ? (sync.last_success_at && sync.schedule_enabled ? "live" : "prototype") : "disabled",
    },
  ];
}

function HealthStrip({ items }: { items: HealthItem[] }) {
  return (
    <section aria-label="System health" className="surface">
      <ul className="stagger-children grid grid-cols-1 divide-y divide-white/[0.05] sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-5 lg:divide-x">
        {items.map((h, i) => (
          <li
            key={h.label}
            className={`group/health relative min-w-0 px-4 py-3.5 transition-colors duration-200 hover:bg-white/[0.02] ${i >= 2 ? "sm:border-t sm:border-white/[0.05] lg:border-t-0" : ""}`}
          >
            {h.action && <div className="absolute top-2.5 right-2.5">{h.action}</div>}
            <div className="flex items-start gap-3">
              {(() => {
                const Icon = HEALTH_ICONS[h.label] ?? Activity;
                return (
                  <span
                    className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-transform duration-300 group-hover/health:scale-110 ${TONE_ICON_CLASS[h.tone]}`}
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                );
              })()}
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-[11px] font-medium text-slate-500">
                  <StatusDot tone={h.tone} pulse={h.tone === "live" && i === 0} />
                  {h.label}
                </p>
                <p className="mt-1 truncate text-sm font-semibold text-slate-100">{h.value}</p>
                <p className="mt-0.5 truncate text-[11px] text-slate-500" title={h.detail}>
                  {h.detail}
                </p>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

const PIPELINE_STEPS = [
  { key: "drafts", label: "Drafted", icon: FileText },
  { key: "awaiting", label: "Awaiting approval", icon: ShieldCheck },
  { key: "approved", label: "Approved, not scheduled", icon: CheckCircle2 },
  { key: "scheduled", label: "Scheduled", icon: CalendarClock },
  { key: "published", label: "Published", icon: Send },
] as const;

function PipelineCard({
  pipeline,
  loading,
  buildHref,
}: {
  pipeline: PipelineSummary | null;
  loading: boolean;
  buildHref: (p: string) => string;
}) {
  const by = pipeline?.content.by_status ?? {};
  const counts: Record<string, number | null> = pipeline
    ? {
        drafts: (by.DRAFTED ?? 0) + (by.RENDERED ?? 0),
        awaiting: pipeline.content.awaiting_approval,
        approved: pipeline.content.approved_unscheduled,
        scheduled: pipeline.publishing.scheduled,
        published: pipeline.publishing.published,
      }
    : { drafts: null, awaiting: null, approved: null, scheduled: null, published: null };

  return (
    <SurfaceCard
      icon={<Send />} title="Publishing pipeline"
      subtitle={
        pipeline
          ? `${formatInt(pipeline.content.total)} content packages · platform posts counted per channel`
          : "Content production, approval and publishing"
      }
      badge={
        <Link
          href={buildHref("/approvals")}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-400/10"
        >
          Open approvals <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      }
    >
      <ol className="stagger-children grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {PIPELINE_STEPS.map((step, idx) => {
          const Icon = step.icon;
          const n = counts[step.key];
          const highlight = step.key === "awaiting" && (n ?? 0) > 0;
          return (
            <li
              key={step.key}
              className={`surface-interactive tilt group relative overflow-hidden rounded-lg border px-3 py-3 ${
                highlight
                  ? "border-sky-400/30 bg-sky-400/[0.06] shadow-[0_0_24px_-10px_rgba(34,211,238,0.6)]"
                  : "border-white/[0.05] bg-[var(--surface-inset)]"
              }`}
            >
              {/* Marching flow line along the bottom edge: content moving to the next stage */}
              {idx < PIPELINE_STEPS.length - 1 && (
                <span
                  aria-hidden="true"
                  className="flow-line pointer-events-none absolute right-0 bottom-0 left-0 h-px opacity-0 transition-opacity duration-300 group-hover:opacity-100"
                />
              )}
              {(n ?? 0) > 0 && (
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute -top-8 -right-8 h-20 w-20 rounded-full bg-[radial-gradient(closest-side,rgba(34,211,238,0.16),transparent)]"
                />
              )}
              <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-md border transition-colors ${
                    highlight
                      ? "border-sky-400/30 bg-sky-400/10 text-sky-300"
                      : "border-white/[0.06] bg-white/[0.02] text-slate-400 group-hover:text-slate-200"
                  }`}
                >
                  <Icon className="h-3 w-3" aria-hidden="true" />
                </span>
                {step.label}
              </p>
              <p className="kpi-number mt-2.5 text-2xl text-slate-50">
                {loading && n === null ? <Skeleton className="h-6 w-8" /> : n === null ? "—" : formatInt(n)}
              </p>
            </li>
          );
        })}
      </ol>
      {pipeline && (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
          <span>
            Next post:{" "}
            <span className="text-slate-300">
              {pipeline.publishing.next_scheduled_at
                ? `${formatDateTime(pipeline.publishing.next_scheduled_at)} IST · ${PLATFORM_LABEL[pipeline.publishing.next_scheduled_platform ?? ""] ?? ""} (${relativeTime(pipeline.publishing.next_scheduled_at)})`
                : "nothing scheduled"}
            </span>
          </span>
          {(pipeline.publishing.overdue ?? 0) > 0 && (
            <Link href={buildHref("/calendar")} className="inline-flex items-center gap-1 text-amber-300 hover:text-amber-200">
              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
              {pipeline.publishing.overdue} overdue (not published 30 min after its time)
            </Link>
          )}
          {pipeline.publishing.failed > 0 && (
            <span className="inline-flex items-center gap-1 text-rose-300">
              <XCircle className="h-3 w-3" aria-hidden="true" />
              {pipeline.publishing.failed} failed
            </span>
          )}
          {pipeline.publishing.cancelled > 0 && <span>{pipeline.publishing.cancelled} cancelled</span>}
        </div>
      )}
    </SurfaceCard>
  );
}

function ActivityCard({ pipeline }: { pipeline: PipelineSummary | null }) {
  const items = pipeline?.recent_activity ?? [];
  return (
    <SurfaceCard icon={<Activity />} title="Recent content activity" subtitle="Latest workflow transitions for this brand">
      {!pipeline ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-9" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="py-6 text-center text-xs text-slate-500">No content activity recorded yet.</p>
      ) : (
        <ol className="relative space-y-3 border-l border-white/[0.06] pl-4">
          {items.map((a, i) => (
            <li key={`${a.content_package_id}-${a.at}-${i}`} className="relative">
              <span
                aria-hidden="true"
                className={`absolute top-1.5 -left-[21px] h-2 w-2 rounded-full ring-4 ring-[var(--surface-panel)] ${
                  a.to_status === "PUBLISHED"
                    ? "bg-emerald-400"
                    : a.to_status.startsWith("FAILED") || a.to_status === "REJECTED"
                      ? "bg-rose-400"
                      : "bg-slate-500"
                }`}
              />
              <p className="bn line-clamp-1 text-xs text-slate-200" lang="bn">
                {a.title}
              </p>
              <p className="mt-0.5 text-[11px] text-slate-500">
                {a.from_status ? `${humanizeEnum(a.from_status)} → ` : ""}
                <span className="text-slate-300">{humanizeEnum(a.to_status)}</span> ·{" "}
                {relativeTime(a.at)}
              </p>
            </li>
          ))}
        </ol>
      )}
    </SurfaceCard>
  );
}

function ConnectorsCard({
  pipeline,
  status,
  buildHref,
}: {
  pipeline: PipelineSummary | null;
  status: SystemStatusResponse | null;
  buildHref: (p: string) => string;
}) {
  const channels = pipeline?.channels ?? [];
  const research = pipeline?.research ?? [];
  return (
    <SurfaceCard
      icon={<Plug />} title="Connectors"
      subtitle="Social channels via Postiz and research sources"
      badge={
        <Link
          href={buildHref("/integrations")}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-400/10"
        >
          Integrations <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      }
    >
      <p className="mb-2 text-[11px] font-medium text-slate-500">Social channels</p>
      {pipeline && channels.length === 0 ? (
        <p className="mb-4 text-xs text-slate-500">No social channels connected for this brand.</p>
      ) : (
        <ul className="mb-4 space-y-1.5">
          {channels.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-3 text-xs">
              <span className="min-w-0 truncate text-slate-200">
                {PLATFORM_LABEL[c.platform] ?? c.platform}
                <span className="text-slate-500"> · {c.name}</span>
              </span>
              <Badge tone={c.status === "CONNECTED" ? "live" : c.status === "ERROR" ? "blocked" : "disabled"} dot>
                {humanizeEnum(c.status)}
              </Badge>
            </li>
          ))}
        </ul>
      )}
      <p className="mb-2 text-[11px] font-medium text-slate-500">Research sources</p>
      <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {research.map((r) => (
          <li key={r.code} className="flex items-center justify-between gap-2 text-xs">
            <span className="truncate text-slate-300">{r.name}</span>
            <Badge tone={r.mode === "BLOCKED" ? "blocked" : r.mode === "OFF" ? "disabled" : "live"}>
              {humanizeEnum(r.mode)}
            </Badge>
          </li>
        ))}
      </ul>
      {status && (
        <p className="mt-4 text-[11px] text-slate-500">
          Postiz {status.postiz.status} · separate deployment, public API only
        </p>
      )}
    </SurfaceCard>
  );
}

export function DashboardOverviewView({
  systemStatus,
  errorMessage,
  initialViews,
  initialPipeline,
}: DashboardOverviewViewProps) {
  const { activeBrand, buildHrefWithBrand } = useBrandContext();
  const [range, setRange] = useState<RangeKey>(DEFAULT_RANGE);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const brandId = activeBrand.brand_id;

  const views = useApiResource<ViewsReport>(viewsPath(brandId, range), initialViews);
  const pipeline = useApiResource<PipelineSummary>(pipelinePath(brandId), initialPipeline);
  const report = views.data && views.data.brand_id === brandId ? views.data : null;
  const pipe = pipeline.data && pipeline.data.brand_id === brandId ? pipeline.data : null;

  return (
    <div className="space-y-6">
      {errorMessage && <ErrorBanner title="Control-plane telemetry warning" message={errorMessage} />}

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[11px] font-medium tracking-[0.1em] text-sky-300/80 uppercase">
            <StatusDot tone={activeBrand.active ? "live" : "disabled"} />
            Brand workspace
          </p>
          <div className="mt-2 flex items-center gap-4">
            <BrandLogoEditor brand={activeBrand} size={60} />
            <h1 className="text-gradient min-w-0 text-[1.75rem] leading-tight font-semibold sm:text-[2.25rem]">
              {activeBrand.name}
            </h1>
          </div>
          <p className="mt-1.5 text-[13px] text-slate-400">{activeBrand.niche}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href={buildHrefWithBrand("/analytics")}
            className="glass-chip btn-press inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-medium text-slate-100 hover:border-white/[0.22] hover:bg-white/[0.09]"
          >
            <LineChart className="h-3.5 w-3.5" aria-hidden="true" /> Analytics
          </Link>
          <Link
            href={buildHrefWithBrand("/approvals")}
            className="btn-press btn-shine inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-b from-sky-300 to-sky-400 px-3.5 py-2 text-xs font-semibold text-[#04121a] shadow-[0_0_0_1px_rgba(34,211,238,0.5),0_8px_20px_-8px_rgba(34,211,238,0.7)] hover:from-sky-200 hover:to-sky-300 hover:shadow-[0_0_0_1px_rgba(103,232,249,0.7),0_10px_28px_-8px_rgba(34,211,238,0.85)]"
          >
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            Review queue
            {pipe && pipe.content.awaiting_approval > 0 && (
              <span className="rounded bg-black/15 px-1.5 tabular">{pipe.content.awaiting_approval}</span>
            )}
          </Link>
        </div>
      </div>

      <NextPostCountdown pipeline={pipe} buildHref={buildHrefWithBrand} />

      <HealthStrip items={healthItems(systemStatus, pipe, report)} />

      <section aria-labelledby="views-heading">
        <SectionHeader
          id="views-heading"
          title="Audience attention"
          description="Platform-reported views across every published post for this brand"
        />
        <div className="surface surface-hero p-5 sm:p-6">
          {views.error && !report && (
            <div className="mb-4">
              <ErrorBanner title="Views unavailable" message={views.error} />
            </div>
          )}
          <ViewsHero report={report} loading={views.loading} range={range} onRange={setRange} />
          <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
            <div className="min-w-0 xl:col-span-2">
              {report ? (
                <>
                  <NoAnalyticsYet report={report} brandName={activeBrand.name} />
                  <div className={`transition-opacity duration-200 ${views.loading ? "opacity-60" : ""}`}>
                    <ViewsTrendChart report={report} />
                  </div>
                </>
              ) : (
                <Skeleton className="h-[220px]" />
              )}
            </div>
            <div className="grid min-w-0 grid-cols-2 content-start gap-x-4 gap-y-5 border-white/[0.05] xl:border-l xl:pl-6">
              <StatTile label="Contributing posts" value={report?.totals.contributing_posts ?? null} hint={report ? `of ${report.totals.published_posts} published` : undefined} />
              <StatTile
                label="Avg views / post"
                value={report?.totals.avg_views_per_post ?? null}
                tip="Total views in the period divided by published platform posts that report views."
              />
              <div className="col-span-2">
                <p className="mb-2 text-[11px] font-medium text-slate-500">Top post this period</p>
                {report ? (
                  <TopPostCard report={report} brandId={brandId} />
                ) : (
                  <Skeleton className="h-14" />
                )}
              </div>
            </div>
          </div>
          {report && (
            <div className="mt-6 grid grid-cols-1 gap-6 border-t border-white/[0.05] pt-5 md:grid-cols-2">
              <div>
                <p className="mb-3 text-[11px] font-medium text-slate-500">Views by platform</p>
                <PlatformBreakdown report={report} />
              </div>
              <div>
                <p className="mb-3 text-[11px] font-medium text-slate-500">Views by social account</p>
                <AccountBreakdown report={report} />
              </div>
            </div>
          )}
          {report && (
            <div className="mt-5 border-t border-white/[0.05] pt-3">
              <SyncLine sync={report.sync} report={report} onSynced={views.reload} />
            </div>
          )}
        </div>
      </section>

      <PipelineCard pipeline={pipe} loading={pipeline.loading} buildHref={buildHrefWithBrand} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ActivityCard pipeline={pipe} />
        <ConnectorsCard pipeline={pipe} status={systemStatus} buildHref={buildHrefWithBrand} />
      </div>

      <section className="surface">
        <button
          type="button"
          aria-expanded={detailsOpen}
          aria-controls="system-details"
          onClick={() => setDetailsOpen((o) => !o)}
          className="flex w-full items-center justify-between gap-3 rounded-[var(--radius-lg)] px-5 py-4 text-left transition-colors hover:bg-white/[0.015]"
        >
          <span>
            <span className="flex items-center gap-2 text-sm font-semibold text-slate-100">
              <Activity className="h-4 w-4 text-slate-400" aria-hidden="true" /> System details
            </span>
            <span className="mt-0.5 block text-xs text-slate-500">
              Module boundaries, brand specification and delivery phases
            </span>
          </span>
          <ChevronDown
            className={`h-4 w-4 text-slate-500 transition-transform duration-200 ${detailsOpen ? "rotate-180" : ""}`}
            aria-hidden="true"
          />
        </button>
        {detailsOpen && (
          <div id="system-details" className="animate-fade-in grid grid-cols-1 gap-6 border-t border-white/[0.05] p-5 lg:grid-cols-5">
            <div className="space-y-3 text-xs lg:col-span-2">
              <div>
                <p className="text-slate-500">Brand slug & ID</p>
                <p className="mt-0.5 font-mono text-[11px] text-slate-300">
                  {activeBrand.slug} ({activeBrand.brand_id})
                </p>
              </div>
              <div>
                <p className="text-slate-500">Description</p>
                <p className="mt-0.5 leading-relaxed text-slate-300">{activeBrand.description}</p>
              </div>
              {activeBrand.target_audience && (
                <div>
                  <p className="text-slate-500">Audience & timezone</p>
                  <p className="mt-0.5 text-slate-300">
                    {activeBrand.target_audience} · <span className="font-mono">{activeBrand.timezone}</span>
                  </p>
                </div>
              )}
              <div>
                <p className="text-slate-500">Content pillars</p>
                <ul className="mt-1.5 flex flex-wrap gap-1.5">
                  {activeBrand.content_pillars.map((pillar) => (
                    <li key={pillar}>
                      <Badge tone="neutral">{pillar}</Badge>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="lg:col-span-3">
              {systemStatus ? (
                <ul className="divide-y divide-white/[0.05] rounded-lg border border-white/[0.05]">
                  {systemStatus.modules.map((mod) => {
                    const isLive = mod.status === "active_phase_3" || mod.status === "active_phase_4";
                    return (
                      <li key={mod.module_id} className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-2.5 text-xs">
                        <div className="min-w-0 flex-1">
                          <p className="font-medium text-slate-200">{mod.title}</p>
                          <p className="mt-0.5 text-[11px] text-slate-500">{mod.summary}</p>
                        </div>
                        <Badge tone={isLive ? "live" : mod.status === "blocked_by_policy" ? "blocked" : "disabled"}>
                          {isLive ? "Active" : mod.status === "blocked_by_policy" ? "Blocked" : mod.target_phase}
                        </Badge>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-xs text-slate-500">Module status could not be loaded from /api/v1/system/status.</p>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
