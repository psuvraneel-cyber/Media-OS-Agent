// Shared by server pages (prefetch) and client views (refetch) so both produce identical keys.

export type RangeKey = "7d" | "30d" | "60d" | "all";
export const DEFAULT_RANGE: RangeKey = "30d";

export interface AnalyticsFilters {
  platform?: string;
  account?: string;
  format?: string;
}

export function buildQuery(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  }
  return q.toString();
}

export function viewsPath(brand: string, range: RangeKey, f: AnalyticsFilters = {}): string {
  return `/analytics/views?${buildQuery({ brand, range, ...f })}`;
}

export function postsPath(
  brand: string,
  range: RangeKey,
  sort: string,
  page: number,
  f: AnalyticsFilters = {},
  pageSize = 10,
): string {
  return `/analytics/posts?${buildQuery({ brand, range, sort, page, page_size: pageSize, ...f })}`;
}

export function postDetailPath(brand: string, packageId: string, range: RangeKey): string {
  return `/analytics/posts/${encodeURIComponent(packageId)}?${buildQuery({ brand, range })}`;
}

export function pipelinePath(brand: string): string {
  return `/analytics/pipeline?${buildQuery({ brand })}`;
}

export function assetUrl(brand: string, assetId: string): string {
  return `/api/v1/content/assets/${encodeURIComponent(assetId)}/file?${buildQuery({ brand })}`;
}
