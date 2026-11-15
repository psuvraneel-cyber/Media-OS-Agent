"use client";

import React, { useEffect, useState } from "react";
import {
  ExternalLink,
  ListFilter,
  Radar,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner, LoadingSkeleton } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";
import type {
  ResearchItemListResponse,
  ResearchSourceItem,
  ResearchSourceListResponse,
  TopicDetailResponse,
  TopicListResponse,
} from "@/lib/types";

const EDITORIAL_STATUSES = [
  "PENDING_EVALUATION",
  "CONSIDERED",
  "SHORTLISTED",
  "APPROVED_FOR_RESEARCH",
  "REJECTED",
  "EXPIRED",
] as const;

function modeTone(mode: string): BadgeTone {
  if (mode === "ENABLED") return "live";
  if (mode === "EXPERIMENTAL") return "prototype";
  if (mode === "BLOCKED") return "blocked";
  return "disabled";
}

function healthTone(health: string): BadgeTone {
  if (health === "HEALTHY") return "live";
  if (health === "DEGRADED") return "prototype";
  if (health === "FAILING" || health === "BLOCKED") return "blocked";
  return "disabled";
}

function editorialTone(status: string): BadgeTone {
  if (status === "APPROVED_FOR_RESEARCH" || status === "SHORTLISTED") return "live";
  if (status === "REJECTED" || status === "EXPIRED") return "blocked";
  if (status === "CONSIDERED") return "info";
  return "disabled";
}

function fmt(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toISOString().replace("T", " ").slice(0, 16) + "Z";
}

/** External links only for http(s) URLs; anything else renders as plain text. */
function SafeExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  if (!/^https?:\/\//i.test(href)) {
    return <span>{children}</span>;
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="inline-flex items-center gap-1 text-sky-300 hover:underline"
    >
      {children}
      <ExternalLink className="h-3 w-3" aria-hidden="true" />
    </a>
  );
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiClientError ? `${err.code}: ${err.message}` : fallback;
}

function ItemRow({ item }: { item: ResearchSourceItem }) {
  return (
    <li className="space-y-1 rounded-lg border border-white/[0.06] bg-[var(--surface-inset)] p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">{item.provider}</Badge>
        <SafeExternalLink href={item.canonical_url}>
          <span className="font-medium text-slate-100">{item.title}</span>
        </SafeExternalLink>
      </div>
      {item.summary && <p className="text-slate-400">{item.summary}</p>}
      <p className="font-mono text-[11px] text-slate-500">
        fetched {fmt(item.fetched_at_utc)} · published {fmt(item.published_at_utc)}
        {item.clusters.length > 0 && <> · cluster {item.clusters[0].dedupe_key}</>}
      </p>
    </li>
  );
}

export function ResearchView() {
  const [refreshTick, setRefreshTick] = useState(0);
  const [result, setResult] = useState<{
    tick: number;
    sources: ResearchSourceListResponse | null;
    error: string | null;
  } | null>(null);
  const [items, setItems] = useState<ResearchItemListResponse | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      browserApiFetch<ResearchSourceListResponse>("/research/sources"),
      browserApiFetch<ResearchItemListResponse>("/research/items?limit=20"),
    ])
      .then(([src, its]) => {
        if (cancelled) return;
        setItems(its);
        setResult({ tick: refreshTick, sources: src, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setResult({
            tick: refreshTick,
            sources: null,
            error: errorText(err, "Failed to load research data."),
          });
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  const loading = result?.tick !== refreshTick;
  const sources = result?.sources ?? null;
  const error = result?.error ?? moreError;

  function refresh() {
    setMoreError(null);
    setRefreshTick((t) => t + 1);
  }

  async function loadMore() {
    if (!items?.next_before_utc) return;
    setLoadingMore(true);
    try {
      const next = await browserApiFetch<ResearchItemListResponse>(
        `/research/items?limit=20&before=${encodeURIComponent(items.next_before_utc)}`,
      );
      setItems({ ...next, items: [...items.items, ...next.items] });
    } catch (err) {
      setMoreError(errorText(err, "Failed to load more items."));
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <SurfaceCard
        icon={<Radar />} title="Research Sources"
        subtitle="Official APIs and feeds only. A live fetch needs the source mode, owner approval and the worker kill switch."
        badge={
          <div className="flex items-center gap-2">
            {sources && (
              <Badge tone={sources.live_fetch_kill_switch_enabled ? "live" : "disabled"}>
                {sources.live_fetch_kill_switch_enabled ? "Kill switch: live" : "Kill switch: off"}
              </Badge>
            )}
            <button
              type="button"
              onClick={refresh}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg btn-press border border-white/[0.1] bg-white/[0.04] px-2.5 py-1 text-xs font-medium text-slate-200 hover:border-white/[0.18] hover:bg-white/[0.08] disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
              <span>Refresh</span>
            </button>
          </div>
        }
      >
        {loading && !sources ? (
          <LoadingSkeleton rows={3} />
        ) : sources ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-white/[0.06] text-[11px] text-slate-400 uppercase">
                  <th className="py-2 pr-3 font-medium">Source</th>
                  <th className="py-2 pr-3 font-medium">Mode</th>
                  <th className="py-2 pr-3 font-medium">Approved</th>
                  <th className="py-2 pr-3 font-medium">Health</th>
                  <th className="py-2 pr-3 font-medium">Last success</th>
                  <th className="py-2 font-medium">Schedule</th>
                </tr>
              </thead>
              <tbody className="rows-in divide-y divide-white/[0.05]">
                {sources.items.map((s) => (
                  <tr key={s.source} data-testid={`source-${s.source}`}>
                    <td className="py-2 pr-3">
                      <p className="font-semibold text-slate-200">{s.source}</p>
                      <p className="text-[11px] text-slate-500">{s.display_name}</p>
                    </td>
                    <td className="py-2 pr-3">
                      <Badge tone={modeTone(s.policy_mode)}>{s.policy_mode}</Badge>
                    </td>
                    <td className="py-2 pr-3 text-slate-300">{s.live_fetch_approved ? "Yes" : "No"}</td>
                    <td className="py-2 pr-3">
                      <Badge tone={healthTone(s.health_status)}>{s.health_status}</Badge>
                      {s.last_error_code && (
                        <p className="mt-1 font-mono text-[11px] text-amber-300">{s.last_error_code}</p>
                      )}
                    </td>
                    <td className="py-2 pr-3 font-mono text-[11px] text-slate-400">
                      {fmt(s.last_success_at_utc)}
                    </td>
                    <td className="py-2 text-[11px] text-slate-400">
                      {s.schedule
                        ? s.schedule.enabled
                          ? `On · next ${fmt(s.schedule.next_run_at_utc)}`
                          : "Disabled"
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </SurfaceCard>

      <SurfaceCard
        icon={<Sparkles />} title="Recently Discovered"
        subtitle={
          items ? `${items.total} shared source items (newest first, all brands).` : "Shared source items."
        }
      >
        {loading && !items ? (
          <LoadingSkeleton rows={4} />
        ) : items && items.items.length > 0 ? (
          <div className="space-y-3">
            <ul className="space-y-2">
              {items.items.map((item) => (
                <ItemRow key={item.source_item_id} item={item} />
              ))}
            </ul>
            {items.next_before_utc && (
              <button
                type="button"
                onClick={() => void loadMore()}
                disabled={loadingMore}
                className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-slate-200 hover:border-white/[0.18] hover:bg-white/[0.08] disabled:opacity-50"
              >
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            )}
          </div>
        ) : (
          <EmptyState
            icon={<Radar className="h-5 w-5" aria-hidden="true" />}
            title="No discovered items yet"
            description="Sources are OFF until the owner enables them. Nothing here is fabricated."
            phaseBadge="Phase 5"
          />
        )}
      </SurfaceCard>
    </div>
  );
}

export function TopicsView() {
  const { activeBrand } = useBrandContext();
  const [statusFilter, setStatusFilter] = useState<string>("");
  const requestKey = `${activeBrand.brand_id}|${statusFilter}`;
  const [result, setResult] = useState<{
    key: string;
    topics: TopicListResponse | null;
    error: string | null;
  } | null>(null);
  const [detail, setDetail] = useState<{ key: string; data: TopicDetailResponse } | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ brand: activeBrand.brand_id, limit: "50" });
    if (statusFilter) params.set("editorial_status", statusFilter);
    browserApiFetch<TopicListResponse>(`/topics?${params.toString()}`)
      .then((res) => {
        if (!cancelled) setResult({ key: requestKey, topics: res, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setResult({ key: requestKey, topics: null, error: errorText(err, "Failed to load topics.") });
      });
    return () => {
      cancelled = true;
    };
  }, [activeBrand.brand_id, statusFilter, requestKey]);

  const current = result?.key === requestKey ? result : null;
  const loading = current === null;
  const topics = current?.topics ?? null;
  const shownDetail = detail?.key === requestKey ? detail.data : null;
  const error = current?.error ?? detailError;

  async function openTopic(id: string) {
    setDetailError(null);
    try {
      const data = await browserApiFetch<TopicDetailResponse>(
        `/topics/${encodeURIComponent(id)}?brand=${encodeURIComponent(activeBrand.brand_id)}`,
      );
      setDetail({ key: requestKey, data });
    } catch (err) {
      setDetailError(errorText(err, "Failed to load topic."));
    }
  }

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <SurfaceCard
        icon={<ListFilter />} title={`Topics — ${activeBrand.name}`}
        subtitle="Shared topic clusters with this brand's own editorial assignment. Other brands' decisions are never shown here."
        badge={
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <span>Status</span>
            <select
              aria-label="Editorial status filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-md border border-white/[0.1] bg-[var(--surface-inset)] px-2 py-1 text-xs"
            >
              <option value="">All</option>
              {EDITORIAL_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        }
      >
        {loading ? (
          <LoadingSkeleton rows={4} />
        ) : topics && topics.items.length > 0 ? (
          <ul className="space-y-2">
            {topics.items.map((t) => (
              <li key={t.topic_cluster_id}>
                <button
                  type="button"
                  onClick={() => void openTopic(t.topic_cluster_id)}
                  className="w-full space-y-1 rounded-lg border border-white/[0.06] bg-[var(--surface-inset)] p-3 text-left text-xs hover:border-slate-600"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={editorialTone(t.assignment.editorial_status)}>
                      {t.assignment.editorial_status}
                    </Badge>
                    <span className="font-medium text-slate-100">{t.title}</span>
                  </div>
                  <p className="font-mono text-[11px] text-slate-500">
                    {t.providers.join(", ")} · {t.source_count} source{t.source_count === 1 ? "" : "s"} ·
                    relevance {t.assignment.relevance_score ?? "—"} · last seen {fmt(t.last_seen_at_utc)}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={<ListFilter className="h-5 w-5" aria-hidden="true" />}
            title={`No topics for ${activeBrand.name}`}
            description="Topics appear once enabled sources discover stories. Scoring arrives in Phase 6."
            phaseBadge="Phase 5"
            brandLabel={activeBrand.name}
          />
        )}
      </SurfaceCard>

      {shownDetail && (
        <SurfaceCard
          title={shownDetail.topic.title}
          subtitle={`Cluster ${shownDetail.topic.dedupe_key} · discovery ${shownDetail.topic.discovery_status} · first seen ${fmt(shownDetail.topic.first_seen_at_utc)}`}
          badge={
            <Badge tone={editorialTone(shownDetail.topic.assignment.editorial_status)}>
              {shownDetail.topic.assignment.editorial_status}
            </Badge>
          }
        >
          <p className="mb-2 text-xs text-slate-400">Provenance ({shownDetail.sources.length})</p>
          <ul className="space-y-2">
            {shownDetail.sources.map((s) => (
              <ItemRow key={s.source_item_id} item={s} />
            ))}
          </ul>
        </SurfaceCard>
      )}
    </div>
  );
}
