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
  FileText,
  LineChart,
  Send,
  ShieldCheck,
  XCircle,
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
    <section aria-label="System health" className="surface overflow-hidden">
      <ul className="grid grid-cols-1 divide-y divide-white/[0.05] sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-5 lg:divide-x">
        {items.map((h, i) => (
          <li
            key={h.label}
            className={`min-w-0 px-4 py-3.5 ${i >= 2 ? "sm:border-t sm:border-white/[0.05] lg:border-t-0" : ""}`}
          >
            <p className="flex items-center gap-2 text-[11px] font-medium text-slate-500">
              <StatusDot tone={h.tone} pulse={h.tone === "live" && i === 0} />
              {h.label}
            </p>
            <p className="mt-1 truncate text-sm font-semibold text-slate-100">{h.value}</p>
            <p className="mt-0.5 truncate text-[11px] text-slate-500" title={h.detail}>
              {h.detail}
            </p>
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
      title="Publishing pipeline"
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
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {PIPELINE_STEPS.map((step) => {
          const Icon = step.icon;
          const n = counts[step.key];
          const highlight = step.key === "awaiting" && (n ?? 0) > 0;
          return (
            <li
              key={step.key}
              className={`rounded-lg border px-3 py-3 ${
                highlight ? "border-sky-400/25 bg-sky-400/[0.05]" : "border-white/[0.05] bg-[var(--surface-inset)]"
              }`}
            >
              <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
                <Icon className={`h-3.5 w-3.5 ${highlight ? "text-sky-300" : ""}`} aria-hidden="true" />
                {step.label}
              </p>
              <p className="mt-1.5 text-xl font-semibold tracking-tight text-slate-50 tabular">
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
    <SurfaceCard title="Recent content activity" subtitle="Latest workflow transitions for this brand">
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
      title="Connectors"
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
          <p className="text-[11px] font-medium tracking-[0.08em] text-slate-500 uppercase">Brand workspace</p>
          <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-50">{activeBrand.name}</h2>
          <p className="mt-0.5 text-[13px] text-slate-400">{activeBrand.niche}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href={buildHrefWithBrand("/analytics")}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-[var(--surface-panel)] px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:border-white/[0.16] hover:bg-[var(--surface-elevated)]"
          >
            <LineChart className="h-3.5 w-3.5" aria-hidden="true" /> Analytics
          </Link>
          <Link
            href={buildHrefWithBrand("/approvals")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-sky-400 px-3 py-1.5 text-xs font-semibold text-[#04121a] transition-colors hover:bg-sky-300"
          >
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            Review queue
            {pipe && pipe.content.awaiting_approval > 0 && (
              <span className="rounded bg-black/15 px-1.5 tabular">{pipe.content.awaiting_approval}</span>
            )}
          </Link>
        </div>
      </div>

      <HealthStrip items={healthItems(systemStatus, pipe, report)} />

      <section aria-labelledby="views-heading">
        <SectionHeader
          id="views-heading"
          title="Audience attention"
          description="Platform-reported views across every published post for this brand"
        />
        <div className="surface p-5">
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
              <SyncLine sync={report.sync} report={report} />
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
          className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left"
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
