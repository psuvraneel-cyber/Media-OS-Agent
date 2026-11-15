"use client";

import { useEffect, useRef, useState } from "react";

import { ApiClientError, browserApiFetch } from "@/lib/api-client";

import type { RangeKey } from "@/lib/analytics-paths";

export type { RangeKey };
export type Granularity = "day" | "week" | "month";

export const RANGE_OPTIONS: ReadonlyArray<{ value: RangeKey; label: string }> = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "60d", label: "60 days" },
  { value: "all", label: "All time" },
];

export const RANGE_LABEL: Record<RangeKey, string> = {
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "60d": "Last 60 days",
  all: "All time",
};

export interface SyncStatus {
  last_success_at: string | null;
  last_attempt_at: string | null;
  last_status: string | null;
  last_error_code: string | null;
  schedule_enabled: boolean;
  schedule_configured: boolean;
  source: string;
}

export interface PostRow {
  schedule_id: string;
  content_package_id: string;
  title: string;
  platform: string;
  channel_account_id: string;
  account_name: string;
  format: string;
  language: string | null;
  hook_type: string | null;
  published_at: string | null;
  status: string;
  post_url: string | null;
  views_in_period: number | null;
  views_approximate: boolean;
  lifetime_views: number | null;
  engagement_in_period: number | null;
  engagement_approximate: boolean;
  lifetime_engagement: number | null;
  engagement_rate: number | null;
  unique_viewers_lifetime: number | null;
  has_metrics: boolean;
  metrics_status: "OK" | "NO_DATA" | "MISSING" | "ERROR" | null;
  metrics_detail: string | null;
  metrics_checked_at: string | null;
  first_observed_at: string | null;
  last_observed_at: string | null;
  thumbnail_asset_id?: string | null;
}

export interface Breakdown {
  value: string;
  views: number | null;
  posts: number;
}

export interface ViewsReport {
  brand_id: string;
  range: RangeKey;
  timezone: string;
  generated_at: string;
  window: { start: string; end: string };
  previous_window: { start: string; end: string } | null;
  granularity: Granularity;
  totals: {
    views: number | null;
    views_previous: number | null;
    views_growth_pct: number | null;
    engagement: number | null;
    engagement_previous: number | null;
    engagement_growth_pct: number | null;
    approximate: boolean;
    published_posts: number;
    published_in_period: number;
    posts_with_view_data: number;
    contributing_posts: number;
    avg_views_per_post: number | null;
  };
  trend: Array<{
    date: string;
    start: string;
    end: string;
    views: number | null;
    engagement: number | null;
  }>;
  by_platform: Array<{
    platform: string;
    views: number | null;
    unique_viewers: number | null;
    reach: number | null;
    engagement: number | null;
    published_posts: number;
    posts_with_views: number;
    view_definition: string;
    included_in_total: boolean;
  }>;
  by_account: Array<{
    channel_account_id: string;
    name: string;
    platform: string;
    views: number | null;
    unique_viewers: number | null;
    published_posts: number;
    posts_with_views: number;
  }>;
  top_post: (PostRow & { thumbnail_asset_id: string | null }) | null;
  breakdowns: Record<string, Breakdown[]>;
  coverage: {
    tracking_since: string | null;
    last_observation_at: string | null;
    notes: string[];
    no_data_posts?: number;
    missing_posts?: number;
    error_posts?: number;
    unchecked_posts?: number;
  };
  sync: SyncStatus;
}

export interface PostsPage {
  range: RangeKey;
  window: { start: string; end: string };
  sort: string;
  page: number;
  page_size: number;
  total: number;
  items: PostRow[];
}

export interface PostDetail {
  content_package_id: string;
  title: string;
  format: string;
  tags: Record<string, string | null>;
  thumbnail_asset_id: string | null;
  range: RangeKey;
  window: { start: string; end: string };
  granularity: Granularity;
  aggregate: { lifetime_views: number | null; views_in_period: number | null; platforms: number };
  platforms: Array<
    PostRow & {
      view_definition: string;
      metrics: Array<{
        key: string;
        label: string;
        lifetime: number | null;
        in_period: number | null;
        approximate: boolean;
      }>;
      trend: Array<{ date: string; views: number | null }>;
      history: Array<{ observed_at: string; views: number }>;
    }
  >;
  sync: SyncStatus;
}

export interface PipelineSummary {
  brand_id: string;
  content: {
    total: number;
    by_status: Record<string, number>;
    awaiting_approval: number;
    approved_unscheduled: number;
  };
  publishing: {
    scheduled: number;
    published: number;
    failed: number;
    overdue?: number;
    cancelled: number;
    next_scheduled_at: string | null;
    next_scheduled_platform: string | null;
  };
  channels: Array<{ id: string; platform: string; name: string; status: string }>;
  research: Array<{
    code: string;
    name: string;
    mode: string;
    health: string;
    last_success_at: string | null;
  }>;
  recent_activity: Array<{
    at: string | null;
    content_package_id: string;
    title: string;
    from_status: string | null;
    to_status: string;
    actor: string;
  }>;
}

/** Server-prefetched result for one request path; the client reuses it when the path matches. */
export interface Initial<T> {
  path: string;
  data: T | null;
  error?: string;
}

interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

const inflight = new Map<string, Promise<unknown>>();

/** Fetch `path` whenever it changes; dedupes concurrent identical requests, ignores stale ones. */
export function useApiResource<T>(
  path: string | null,
  initial?: Initial<T>,
): ApiState<T> & { reload: () => void } {
  const seeded = initial && initial.path === path;
  const [state, setState] = useState<ApiState<T>>({
    data: seeded ? initial.data : null,
    error: seeded ? (initial.error ?? null) : null,
    loading: !seeded && path !== null,
  });
  const [nonce, setNonce] = useState(0);
  const skipFirst = useRef(Boolean(seeded));

  useEffect(() => {
    if (path === null) return;
    if (skipFirst.current) {
      skipFirst.current = false;
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    const key = `${path}#${nonce}`;
    let promise = inflight.get(key) as Promise<T> | undefined;
    if (!promise) {
      promise = browserApiFetch<T>(path);
      inflight.set(key, promise);
      void promise.finally(() => inflight.delete(key)).catch(() => undefined);
    }
    promise
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof ApiClientError ? err.message : "Analytics service is unreachable.";
        setState((s) => ({ ...s, error: message, loading: false }));
      });
    return () => {
      cancelled = true;
    };
  }, [path, nonce]);

  return { ...state, reload: () => setNonce((n) => n + 1) };
}
