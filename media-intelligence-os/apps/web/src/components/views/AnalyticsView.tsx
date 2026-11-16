"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Info,
  LayoutGrid,
  LineChart,
  Users,
  X,
} from "lucide-react";

import {
  PLATFORM_COLOR,
  PlatformMark,
  PostLink,
  PostThumb,
  RangeControl,
  StatTile,
  StatusBadge,
  SyncLine,
  VIEWS_TOOLTIP,
  ViewsTrendChart,
} from "@/components/analytics/ViewsPanels";
import { SyncNowButton } from "@/components/analytics/SyncNowButton";
import { AreaChart, BarList } from "@/components/charts/AreaChart";
import { Badge } from "@/components/ui/Badge";
import { DeltaPill, InfoTip, SegmentedControl, Select } from "@/components/ui/Controls";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner, Skeleton } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import {
  RANGE_LABEL,
  useApiResource,
  type Initial,
  type PipelineSummary,
  type PostDetail,
  type PostRow,
  type PostsPage,
  type RangeKey,
  type ViewsReport,
} from "@/lib/analytics";
import {
  DEFAULT_RANGE,
  pipelinePath,
  postDetailPath,
  postsPath,
  viewsPath,
  type AnalyticsFilters,
} from "@/lib/analytics-paths";
import { useBrandContext } from "@/lib/brand-context";
import {
  PLATFORM_LABEL,
  formatBucket,
  formatCompact,
  formatDate,
  formatDateTime,
  formatInt,
  formatPct,
  humanizeEnum,
  relativeTime,
} from "@/lib/format";

type SortKey = "views" | "engagement_rate" | "published";
const PAGE_SIZE = 10;
const ALL_PLATFORMS = ["INSTAGRAM", "FACEBOOK", "YOUTUBE"] as const;

const BREAKDOWN_TABS = [
  { value: "format", label: "Format" },
  { value: "hook_type", label: "Hook" },
  { value: "pillar", label: "Topic" },
  { value: "occasion", label: "Occasion" },
  { value: "language", label: "Language" },
  { value: "visual_style", label: "Visual" },
  { value: "posting_hour", label: "Posting time" },
] as const;
type BreakdownKey = (typeof BREAKDOWN_TABS)[number]["value"];

interface AnalyticsViewProps {
  initialViews?: Initial<ViewsReport>;
  initialPosts?: Initial<PostsPage>;
  initialPipeline?: Initial<PipelineSummary>;
}

export function AnalyticsView({ initialViews, initialPosts, initialPipeline }: AnalyticsViewProps = {}) {
  const { activeBrand } = useBrandContext();
  const brandId = activeBrand.brand_id;
  const [range, setRange] = useState<RangeKey>(DEFAULT_RANGE);
  const [platform, setPlatform] = useState("");
  const [account, setAccount] = useState("");
  const [format, setFormat] = useState("");
  const [sort, setSort] = useState<SortKey>("views");
  const [page, setPage] = useState(1);
  const [breakdown, setBreakdown] = useState<BreakdownKey>("hook_type");
  const [openPost, setOpenPost] = useState<string | null>(null);

  // Brand switch resets filters that reference the previous brand's accounts (render-time reset).
  const [filtersBrand, setFiltersBrand] = useState(brandId);
  if (filtersBrand !== brandId) {
    setFiltersBrand(brandId);
    setAccount("");
    setPage(1);
    setOpenPost(null);
  }

  const filters: AnalyticsFilters = { platform, account, format };
  const views = useApiResource<ViewsReport>(viewsPath(brandId, range, filters), initialViews);
  const posts = useApiResource<PostsPage>(
    postsPath(brandId, range, sort, page, filters, PAGE_SIZE),
    initialPosts,
  );
  const pipeline = useApiResource<PipelineSummary>(pipelinePath(brandId), initialPipeline);

  const report = views.data && views.data.brand_id === brandId ? views.data : null;
  const channels = pipeline.data && pipeline.data.brand_id === brandId ? pipeline.data.channels : [];

  function change<T>(setter: (v: T) => void) {
    return (v: T) => {
      setter(v);
      setPage(1);
    };
  }

  const reloadAll = () => {
    views.reload();
    posts.reload();
    pipeline.reload();
  };

  const noPosts = report !== null && report.totals.published_posts === 0;
  const filtered = Boolean(platform || account || format);

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="surface flex flex-wrap items-end justify-between gap-4 px-4 py-3">
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-slate-500">Period</span>
          <RangeControl value={range} onChange={change(setRange)} />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Select
            label="Platform"
            value={platform}
            onChange={change(setPlatform)}
            options={[{ value: "", label: "All platforms" }, ...ALL_PLATFORMS.map((p) => ({ value: p, label: PLATFORM_LABEL[p] }))]}
          />
          <Select
            label="Account"
            value={account}
            onChange={change(setAccount)}
            options={[
              { value: "", label: "All accounts" },
              ...channels.map((c) => ({ value: c.id, label: `${c.name} · ${PLATFORM_LABEL[c.platform] ?? c.platform}` })),
            ]}
          />
          <Select
            label="Format"
            value={format}
            onChange={change(setFormat)}
            options={[
              { value: "", label: "All formats" },
              { value: "CAROUSEL", label: "Carousel" },
              { value: "POST", label: "Photo post" },
              { value: "REEL", label: "Reel" },
              { value: "SHORT", label: "Short" },
              { value: "LONG_VIDEO", label: "Long video" },
            ]}
          />
          <SyncNowButton variant="chip" onSynced={reloadAll} />
        </div>
      </div>

      {views.error && !report && <ErrorBanner title="Analytics unavailable" message={views.error} />}

      {report && ((report.coverage.no_data_posts ?? 0) > 0 || (report.coverage.error_posts ?? 0) > 0) && (
        <div role="status" className="rounded-xl border border-amber-400/20 bg-amber-400/[0.05] px-4 py-3 text-xs leading-relaxed text-amber-100">
          {(report.coverage.no_data_posts ?? 0) > 0 && (
            <p>
              {report.coverage.no_data_posts} published post{report.coverage.no_data_posts === 1 ? "" : "s"} returned no
              metrics from the platform. Instagram can reject insights for some media types; Media OS keeps
              retrying once a day and never fills the gap with estimates.
            </p>
          )}
          {(report.coverage.error_posts ?? 0) > 0 && (
            <p className={(report.coverage.no_data_posts ?? 0) > 0 ? "mt-1" : ""}>
              {report.coverage.error_posts} post{report.coverage.error_posts === 1 ? "" : "s"} could not be read on the last
              sync (Postiz error); they are retried on the next run.
            </p>
          )}
        </div>
      )}

      {noPosts && !filtered ? (
        <>
          <EmptyState
            icon={<LineChart className="h-5 w-5" aria-hidden="true" />}
            title={`No performance records collected for ${activeBrand.name}`}
            description={
              <>
                Nothing has been published for this brand yet, so there are no platform-reported views to
                show. Media OS never displays sample figures: views appear after the first post goes live
                and the analytics sync reads it from the platform through Postiz.
              </>
            }
            brandLabel={activeBrand.name}
          />
          {report && (
            <div className="px-1">
              <SyncLine sync={report.sync} report={report} onSynced={reloadAll} />
            </div>
          )}
        </>
      ) : (
        <>
          {/* A. Overview KPIs */}
          <section aria-label="Overview" className="stagger-children grid grid-cols-1 gap-4 lg:grid-cols-4">
            <div className="surface surface-hero p-5 lg:col-span-2">
              <p className="flex items-center gap-1.5 text-xs font-medium text-slate-400">
                Total views · {RANGE_LABEL[range]}
                <InfoTip text={VIEWS_TOOLTIP} label="How total views are counted" />
              </p>
              {report ? (
                <>
                  <p className="mt-2 text-4xl font-semibold tracking-tight text-slate-50 tabular">
                    {report.totals.approximate && <span className="mr-1 text-2xl text-slate-500" title="Estimated at period edges">≈</span>}
                    {formatInt(report.totals.views)}
                  </p>
                  <div className="mt-2">
                    <DeltaPill pct={range === "all" ? null : report.totals.views_growth_pct} />
                  </div>
                </>
              ) : (
                <Skeleton className="mt-2 h-10 w-44" />
              )}
            </div>
            <div className="surface grid grid-cols-2 gap-5 p-5 lg:col-span-2">
              <StatTile
                label="Engagement"
                value={report?.totals.engagement ?? null}
                tip="Likes, reactions, comments, shares and saves received in the period, where the platform reports them."
                hint={report && range !== "all" ? <DeltaPill pct={report.totals.engagement_growth_pct} suffix="" /> : undefined}
              />
              <StatTile
                label="Published posts"
                value={report?.totals.published_posts ?? null}
                tip="Platform posts with status Published. A carousel on Instagram and Facebook counts as two."
                hint={report ? `${report.totals.published_in_period} published in period` : undefined}
              />
              <StatTile label="Avg views / post" value={report?.totals.avg_views_per_post ?? null} />
              <StatTile
                label="Posts with views"
                value={report?.totals.contributing_posts ?? null}
                hint={report ? `${report.totals.posts_with_view_data} report a views metric` : undefined}
              />
            </div>
          </section>

          {/* B. Trend */}
          <SurfaceCard
            icon={<LineChart />} title="Views over time"
            subtitle={report ? `Per ${report.granularity} · Asia/Kolkata · ${formatDate(report.window.start)} – ${formatDate(report.window.end)}` : "Loading…"}
            badge={report?.totals.approximate ? <Badge tone="neutral">≈ boundary estimates</Badge> : undefined}
          >
            {report ? (
              <div className={`transition-opacity duration-200 ${views.loading ? "opacity-60" : ""}`}>
                <ViewsTrendChart report={report} height={240} />
              </div>
            ) : (
              <Skeleton className="h-[240px]" />
            )}
          </SurfaceCard>

          {/* C. Platform comparison */}
          <SurfaceCard icon={<BarChart3 />} title="Platform comparison" subtitle="Each platform's own metrics; definitions differ, so totals are never mixed" flush>
            <div className="overflow-x-auto">
              <table aria-label="Platform comparison" className="w-full min-w-[640px] text-left text-xs">
                <thead className="border-y border-white/[0.05] text-[11px] text-slate-500">
                  <tr>
                    <th scope="col" className="px-5 py-2 font-medium">Platform</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Views</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Unique viewers</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Reach</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Engagement</th>
                    <th scope="col" className="px-5 py-2 text-right font-medium">Posts</th>
                  </tr>
                </thead>
                <tbody className="rows-in divide-y divide-white/[0.04]">
                  {ALL_PLATFORMS.map((pf) => {
                    const row = report?.by_platform.find((p) => p.platform === pf);
                    const connected = channels.some((c) => c.platform === pf && c.status === "CONNECTED");
                    return (
                      <tr key={pf} className="transition-colors hover:bg-white/[0.02]">
                        <td className="px-5 py-3">
                          <span className="inline-flex items-center gap-1.5 text-slate-200">
                            <PlatformMark platform={pf} />
                            {row && <InfoTip text={row.view_definition} label={`${PLATFORM_LABEL[pf]} metric definitions`} />}
                          </span>
                        </td>
                        {row ? (
                          <>
                            <td className="px-3 py-3 text-right text-slate-100 tabular">{row.posts_with_views ? formatInt(row.views) : "—"}</td>
                            <td className="px-3 py-3 text-right text-slate-300 tabular">{formatInt(row.unique_viewers)}</td>
                            <td className="px-3 py-3 text-right text-slate-300 tabular">{formatInt(row.reach)}</td>
                            <td className="px-3 py-3 text-right text-slate-300 tabular">{formatInt(row.engagement)}</td>
                            <td className="px-5 py-3 text-right text-slate-300 tabular">{row.published_posts}</td>
                          </>
                        ) : (
                          <td colSpan={5} className="px-5 py-3 text-right text-[11px] text-slate-500">
                            {connected ? "Connected · no published posts in this filter" : "Not connected — no data"}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </SurfaceCard>

          {/* D. Top content */}
          <SurfaceCard
            icon={<LayoutGrid />} title="Content performance"
            subtitle={`Platform posts ranked for ${RANGE_LABEL[range].toLowerCase()} · select a post for details`}
            flush
            badge={
              <SegmentedControl
                label="Sort posts by"
                size="sm"
                value={sort}
                onChange={change(setSort)}
                options={[
                  { value: "views", label: "Views" },
                  { value: "engagement_rate", label: "Engagement rate" },
                  { value: "published", label: "Newest" },
                ]}
              />
            }
          >
            <PostsTable
              data={posts.data}
              loading={posts.loading}
              error={posts.error}
              brandId={brandId}
              onOpen={setOpenPost}
            />
            {posts.data && posts.data.total > PAGE_SIZE && (
              <div className="flex items-center justify-between border-t border-white/[0.05] px-5 py-3 text-xs text-slate-400">
                <span className="tabular">
                  {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, posts.data.total)} of {posts.data.total}
                </span>
                <div className="flex gap-1">
                  <button
                    type="button"
                    disabled={page === 1}
                    onClick={() => setPage((p) => p - 1)}
                    aria-label="Previous page"
                    className="rounded-md border border-white/[0.08] p-1.5 transition-colors hover:bg-white/[0.05] disabled:opacity-40"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    disabled={page * PAGE_SIZE >= posts.data.total}
                    onClick={() => setPage((p) => p + 1)}
                    aria-label="Next page"
                    className="rounded-md border border-white/[0.08] p-1.5 transition-colors hover:bg-white/[0.05] disabled:opacity-40"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}
          </SurfaceCard>

          {/* E. Breakdown */}
          <SurfaceCard
            title="Performance breakdown"
            subtitle="Views in the period grouped by the post-tagging taxonomy. Untagged posts are left out, never guessed."
          >
            <div className="mb-4 overflow-x-auto">
              <SegmentedControl label="Group views by" size="sm" value={breakdown} onChange={setBreakdown} options={BREAKDOWN_TABS} />
            </div>
            {report ? (
              <BarList
                formatValue={formatInt}
                emptyLabel="Not enough tagged posts with views for this breakdown yet"
                items={(report.breakdowns[breakdown] ?? []).map((b) => ({
                  key: b.value,
                  label: breakdown === "posting_hour" ? `${b.value} IST` : humanizeEnum(b.value),
                  value: b.views,
                  hint: `${b.posts} post${b.posts === 1 ? "" : "s"}`,
                }))}
              />
            ) : (
              <Skeleton className="h-24" />
            )}
          </SurfaceCard>

          {/* F. Accounts + methodology */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <SurfaceCard icon={<Users />} title="Views by social account">
              {report ? (
                <BarList
                  formatValue={formatInt}
                  emptyLabel="No account has published posts in this filter"
                  items={report.by_account.map((a) => ({
                    key: a.channel_account_id,
                    label: `${a.name} · ${PLATFORM_LABEL[a.platform] ?? a.platform}`,
                    value: a.posts_with_views > 0 ? a.views : null,
                    hint:
                      a.posts_with_views === 0 && a.unique_viewers !== null
                        ? `${formatInt(a.unique_viewers)} unique viewers`
                        : `${a.published_posts} posts`,
                    color: PLATFORM_COLOR[a.platform],
                  }))}
                />
              ) : (
                <Skeleton className="h-20" />
              )}
            </SurfaceCard>
            <SurfaceCard icon={<Info />} title="How these numbers are calculated">
              {report ? (
                <>
                  <ul className="list-disc space-y-1.5 pl-4 text-xs leading-relaxed text-slate-400">
                    {report.coverage.notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                  <div className="mt-4">
                    <SyncLine sync={report.sync} report={report} onSynced={reloadAll} />
                  </div>
                </>
              ) : (
                <Skeleton className="h-20" />
              )}
            </SurfaceCard>
          </div>
        </>
      )}

      {openPost && (
        <PostDetailDrawer brandId={brandId} packageId={openPost} initialRange={range} onClose={() => setOpenPost(null)} />
      )}
    </div>
  );
}

const SYNC_STATE_LABEL: Record<string, string> = {
  NO_DATA: "No data from platform",
  MISSING: "Removed from platform",
  ERROR: "Sync error",
};

/** Why a post has no numbers yet, with the stored detail as a tooltip. */
function SyncState({ row }: { row: PostRow }) {
  const label = row.metrics_status ? (SYNC_STATE_LABEL[row.metrics_status] ?? "Awaiting sync") : "Awaiting first sync";
  const tone = row.metrics_status && row.metrics_status !== "OK" ? "text-amber-300/90" : "text-slate-500";
  return (
    <span className={`inline-flex items-center justify-end gap-1 font-normal ${tone}`}>
      {label}
      {row.metrics_detail && <InfoTip text={row.metrics_detail} label={`Why: ${label}`} />}
    </span>
  );
}

function PostsTable({
  data,
  loading,
  error,
  brandId,
  onOpen,
}: {
  data: PostsPage | null;
  loading: boolean;
  error: string | null;
  brandId: string;
  onOpen: (packageId: string) => void;
}) {
  if (error && !data) return <p className="px-5 pb-5 text-xs text-rose-300">{error}</p>;
  if (!data) {
    return (
      <div className="space-y-2 px-5 pb-5">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12" />
        ))}
      </div>
    );
  }
  if (data.items.length === 0) {
    return <p className="px-5 pb-6 text-center text-xs text-slate-500">No published posts match these filters.</p>;
  }
  return (
    <div className={`transition-opacity duration-200 ${loading ? "opacity-60" : ""}`}>
      {/* Table ≥ md, cards below */}
      <table aria-label="Content performance" className="hidden w-full text-left text-xs md:table">
        <thead className="border-y border-white/[0.05] text-[11px] text-slate-500">
          <tr>
            <th scope="col" className="px-5 py-2 font-medium">Post</th>
            <th scope="col" className="px-3 py-2 font-medium">Platform · account</th>
            <th scope="col" className="px-3 py-2 font-medium">Published</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Views (period)</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Lifetime</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Eng. rate</th>
            <th scope="col" className="px-5 py-2 text-right font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="rows-in divide-y divide-white/[0.04]">
          {data.items.map((r) => (
            <tr
              key={r.schedule_id}
              className="cursor-pointer transition-colors hover:bg-white/[0.025]"
              onClick={() => onOpen(r.content_package_id)}
            >
              <td className="max-w-[18rem] px-5 py-2.5">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(r.content_package_id);
                  }}
                  className="flex items-center gap-3 text-left"
                >
                  <PostThumb brandId={brandId} assetId={r.thumbnail_asset_id} size={32} />
                  <span className="bn line-clamp-2 text-slate-100" lang="bn">
                    {r.title}
                  </span>
                </button>
              </td>
              <td className="px-3 py-2.5 text-slate-300">
                <PlatformMark platform={r.platform} />
                <span className="block truncate text-[11px] text-slate-500">{r.account_name}</span>
              </td>
              <td className="px-3 py-2.5 whitespace-nowrap text-slate-400">{formatDate(r.published_at)}</td>
              <td className="px-3 py-2.5 text-right font-medium text-slate-100 tabular">
                {r.views_approximate && <span className="text-slate-500">≈</span>}
                {r.has_metrics ? formatInt(r.views_in_period) : <SyncState row={r} />}
              </td>
              <td className="px-3 py-2.5 text-right text-slate-300 tabular">{formatInt(r.lifetime_views)}</td>
              <td className="px-3 py-2.5 text-right text-slate-300 tabular">{formatPct(r.engagement_rate)}</td>
              <td className="px-5 py-2.5 text-right">
                <StatusBadge status={r.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="divide-y divide-white/[0.04] border-t border-white/[0.05] md:hidden">
        {data.items.map((r) => (
          <li key={r.schedule_id}>
            <button type="button" onClick={() => onOpen(r.content_package_id)} className="flex w-full items-start gap-3 px-4 py-3 text-left">
              <PostThumb brandId={brandId} assetId={r.thumbnail_asset_id} size={40} />
              <span className="min-w-0 flex-1">
                <span className="bn line-clamp-2 text-[13px] text-slate-100" lang="bn">
                  {r.title}
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] text-slate-500">
                  <PlatformMark platform={r.platform} />
                  <span>{formatDate(r.published_at)}</span>
                </span>
              </span>
              <span className="shrink-0 text-right">
                <span className="block text-sm font-semibold text-slate-100 tabular">{r.has_metrics ? formatCompact(r.views_in_period) : "—"}</span>
                <span className="text-[10px] text-slate-500">views</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PostDetailDrawer({
  brandId,
  packageId,
  initialRange,
  onClose,
}: {
  brandId: string;
  packageId: string;
  initialRange: RangeKey;
  onClose: () => void;
}) {
  const [range, setRange] = useState<RangeKey>(initialRange);
  const detail = useApiResource<PostDetail>(postDetailPath(brandId, packageId, range));
  const d = detail.data && detail.data.content_package_id === packageId ? detail.data : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const combinedTrend = useMemo(() => {
    if (!d) return [];
    const byDate = new Map<string, number | null>();
    for (const p of d.platforms) {
      for (const pt of p.trend) {
        if (pt.views === null) {
          if (!byDate.has(pt.date)) byDate.set(pt.date, null);
        } else byDate.set(pt.date, (byDate.get(pt.date) ?? 0) + pt.views);
      }
    }
    return [...byDate.entries()].map(([date, value]) => ({
      key: date,
      label: formatBucket(date, d.granularity),
      value,
    }));
  }, [d]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-labelledby="post-detail-title">
      <div className="animate-fade-in absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={onClose} aria-hidden="true" />
      <aside className="animate-slide-in-right relative flex h-full w-full max-w-xl flex-col border-l border-white/[0.1] bg-[rgba(12,14,19,0.78)] shadow-[var(--shadow-lg),inset_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-2xl backdrop-saturate-150">
        <header className="flex items-start justify-between gap-3 border-b border-white/[0.06] px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            <PostThumb brandId={brandId} assetId={d?.thumbnail_asset_id} size={44} />
            <div className="min-w-0">
              <h2 id="post-detail-title" className="bn line-clamp-2 text-sm font-semibold text-slate-50" lang="bn">
                {d?.title ?? "Loading post…"}
              </h2>
              {d && (
                <p className="mt-1 flex flex-wrap gap-1.5">
                  {Object.entries(d.tags)
                    .filter(([, v]) => v)
                    .map(([k, v]) => (
                      <Badge key={k} tone="neutral">
                        {humanizeEnum(v)}
                      </Badge>
                    ))}
                </p>
              )}
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close post analytics" className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-white/[0.06] hover:text-slate-100">
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          <RangeControl value={range} onChange={setRange} size="sm" />
          {detail.error && !d && <ErrorBanner title="Post analytics unavailable" message={detail.error} />}
          {!d ? (
            <div className="space-y-3">
              <Skeleton className="h-16" />
              <Skeleton className="h-40" />
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4 rounded-lg border border-white/[0.06] bg-[var(--surface-inset)] p-4">
                <StatTile label="Lifetime views (all platforms)" value={d.aggregate.lifetime_views} emphasis tip="Sum of each platform's own lifetime views as reported by the platform." />
                <StatTile label={`Views · ${RANGE_LABEL[range]}`} value={d.aggregate.views_in_period} emphasis />
              </div>
              <div className={detail.loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
                <p className="mb-2 text-[11px] font-medium text-slate-500">Views trend (all platforms)</p>
                <AreaChart points={combinedTrend} height={180} formatValue={formatCompact} ariaLabel="Views trend for this post" />
              </div>
              {d.platforms.map((p) => (
                <section key={p.schedule_id} className="rounded-lg border border-white/[0.06]">
                  <header className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.05] px-4 py-2.5">
                    <span className="flex items-center gap-2 text-xs font-medium text-slate-200">
                      <PlatformMark platform={p.platform} />
                      <span className="text-slate-500">· {p.account_name}</span>
                      <InfoTip text={p.view_definition} label="Metric definitions" />
                    </span>
                    <span className="flex items-center gap-2">
                      <PostLink url={p.post_url} />
                      <StatusBadge status={p.status} />
                    </span>
                  </header>
                  {p.metrics.length === 0 ? (
                    <p className="px-4 py-3 text-xs text-slate-500">
                      {p.metrics_status === "NO_DATA" || p.metrics_status === "MISSING" || p.metrics_status === "ERROR"
                        ? p.metrics_detail ?? "The platform returned no metrics."
                        : "No metrics collected yet"}
                      {p.metrics_checked_at ? ` · last checked ${relativeTime(p.metrics_checked_at)}` : ""}
                      {p.published_at ? ` · published ${formatDateTime(p.published_at)}` : ""}.
                    </p>
                  ) : (
                    <table className="w-full text-xs">
                      <thead className="text-[11px] text-slate-500">
                        <tr>
                          <th scope="col" className="px-4 py-2 text-left font-medium">Metric</th>
                          <th scope="col" className="px-4 py-2 text-right font-medium">{RANGE_LABEL[range]}</th>
                          <th scope="col" className="px-4 py-2 text-right font-medium">Lifetime</th>
                        </tr>
                      </thead>
                      <tbody className="rows-in divide-y divide-white/[0.04]">
                        {p.metrics.map((m) => (
                          <tr key={m.key}>
                            <td className="px-4 py-2 text-slate-300">
                              {m.label}
                              {m.key === "unique_viewers" && <span className="ml-1 text-[10px] text-slate-500">(not views)</span>}
                            </td>
                            <td className="px-4 py-2 text-right text-slate-100 tabular">
                              {m.approximate && <span className="text-slate-500">≈</span>}
                              {formatInt(m.in_period)}
                            </td>
                            <td className="px-4 py-2 text-right text-slate-300 tabular">{formatInt(m.lifetime)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  <p className="border-t border-white/[0.05] px-4 py-2 text-[11px] text-slate-500">
                    Tracked since {formatDate(p.first_observed_at)} · last read {relativeTime(p.last_observed_at)}
                    {p.engagement_rate !== null && <> · engagement rate {formatPct(p.engagement_rate)}</>}
                  </p>
                </section>
              ))}
              <SyncLine sync={d.sync} />
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
