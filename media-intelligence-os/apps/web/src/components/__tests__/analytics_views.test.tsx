import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/analytics",
  useSearchParams: () => new URLSearchParams(),
}));

import { AnalyticsView } from "@/components/views/AnalyticsView";
import type { PipelineSummary, PostsPage, ViewsReport } from "@/lib/analytics";
import { accountPath, pipelinePath, postsPath, viewsPath } from "@/lib/analytics-paths";
import { BrandProvider } from "@/lib/brand-context";
import type { BrandProfileSummary } from "@/lib/types";

const BRAND = "99999999-1111-4111-8111-111111111111";
const BRANDS: BrandProfileSummary[] = [
  {
    brand_id: BRAND,
    slug: "brand-app-promo",
    name: "Nimontron",
    niche: "Bengali invitations",
    description: "d",
    active: true,
    data_origin: "postgresql",
    placeholder_unconfigured: false,
    content_pillars: [],
  } as unknown as BrandProfileSummary,
];

const ACCOUNT = {
  window_days: 7 as const,
  combined: { views: 11, new_followers: 1 },
  channels: [
    {
      channel_id: "ig",
      platform: "INSTAGRAM",
      name: "Nimontron IG",
      observed_on: "2026-11-16",
      observed_at: "2026-11-16T16:05:00+00:00",
      metrics: { views: 4321, likes: 87 },
      display: [
        { key: "views", label: "Views", value: 4321 },
        { key: "likes", label: "Likes", value: 87 },
        { key: "new_followers", label: "New followers", value: null },
      ],
    },
  ],
};

const SYNC = {
  last_success_at: "2026-11-15T10:00:00+00:00",
  last_attempt_at: "2026-11-15T10:00:00+00:00",
  last_status: "SUCCEEDED",
  last_error_code: null,
  schedule_enabled: true,
  schedule_configured: true,
  source: "Postiz",
};

const POST = {
  schedule_id: "s-ig",
  content_package_id: "pkg-1",
  title: "বিয়ের কার্ডে দেরি? নিজেই বানান নিমন্ত্রণ।",
  platform: "INSTAGRAM",
  channel_account_id: "c-ig",
  account_name: "Nimontron IG",
  format: "CAROUSEL",
  language: "BN_EN",
  hook_type: "PAIN_POINT",
  published_at: "2026-11-15T13:30:00+00:00",
  status: "PUBLISHED",
  post_url: null,
  views_in_period: 1234,
  views_approximate: false,
  lifetime_views: 1500,
  engagement_in_period: 40,
  engagement_approximate: false,
  lifetime_engagement: 60,
  engagement_rate: 4,
  unique_viewers_lifetime: null,
  has_metrics: true,
  metrics_status: "OK" as const,
  metrics_detail: null,
  metrics_checked_at: "2026-11-16T14:00:00+00:00",
  first_observed_at: "2026-11-15T14:00:00+00:00",
  last_observed_at: "2026-11-16T14:00:00+00:00",
  thumbnail_asset_id: null,
};

function report(views: number, range: ViewsReport["range"] = "30d"): ViewsReport {
  return {
    brand_id: BRAND,
    range,
    timezone: "Asia/Kolkata",
    generated_at: "2026-11-16T00:00:00+00:00",
    window: { start: "2026-10-17T18:30:00+00:00", end: "2026-11-16T00:00:00+00:00" },
    previous_window: null,
    granularity: "day",
    totals: {
      views,
      views_previous: 1000,
      views_growth_pct: 23.4,
      engagement: 40,
      engagement_previous: null,
      engagement_growth_pct: null,
      approximate: false,
      published_posts: 2,
      published_in_period: 2,
      posts_with_view_data: 1,
      contributing_posts: 1,
      avg_views_per_post: views,
    },
    trend: [
      { date: "2026-11-15", start: "", end: "", views: 200, engagement: 5 },
      { date: "2026-11-16", start: "", end: "", views: views - 200, engagement: 35 },
    ],
    by_platform: [
      {
        platform: "INSTAGRAM",
        views,
        unique_viewers: null,
        reach: 900,
        engagement: 40,
        published_posts: 1,
        posts_with_views: 1,
        view_definition: "Instagram views",
        included_in_total: true,
      },
      {
        platform: "FACEBOOK",
        views: 0,
        unique_viewers: 321,
        reach: null,
        engagement: 3,
        published_posts: 1,
        posts_with_views: 0,
        view_definition: "Facebook unique viewers are not views",
        included_in_total: true,
      },
    ],
    by_account: [
      { channel_account_id: "c-ig", name: "Nimontron IG", platform: "INSTAGRAM", views, unique_viewers: null, published_posts: 1, posts_with_views: 1 },
    ],
    top_post: { ...POST, thumbnail_asset_id: null },
    breakdowns: { hook_type: [{ value: "PAIN_POINT", views, posts: 1 }] },
    coverage: { tracking_since: "2026-11-15T14:00:00+00:00", last_observation_at: null, notes: ["Views are platform-reported."] },
    sync: SYNC,
  };
}

const PIPELINE: PipelineSummary = {
  brand_id: BRAND,
  content: { total: 3, by_status: {}, awaiting_approval: 1, approved_unscheduled: 0 },
  publishing: { scheduled: 0, published: 2, failed: 0, cancelled: 0, next_scheduled_at: null, next_scheduled_platform: null },
  channels: [
    { id: "c-ig", platform: "INSTAGRAM", name: "Nimontron IG", status: "CONNECTED" },
    { id: "c-fb", platform: "FACEBOOK", name: "Nimontron FB", status: "CONNECTED" },
  ],
  research: [],
  recent_activity: [],
};

const POSTS: PostsPage = {
  range: "30d",
  window: { start: "", end: "" },
  sort: "views",
  page: 1,
  page_size: 10,
  total: 1,
  items: [POST],
};

function renderView() {
  return render(
    <BrandProvider brands={BRANDS}>
      <AnalyticsView
        initialViews={{ path: viewsPath(BRAND, "30d"), data: report(1234) }}
        initialPosts={{ path: postsPath(BRAND, "30d", "views", 1, {}, 10), data: POSTS }}
        initialPipeline={{ path: pipelinePath(BRAND), data: PIPELINE }}
        initialAccount={{ path: accountPath(BRAND), data: ACCOUNT }}
      />
    </BrandProvider>,
  );
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("Views analytics page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders server data without refetching and never adds Facebook unique viewers to views", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderView();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getAllByText("1,234").length).toBeGreaterThan(0);
    expect(screen.getByText("+23.4%")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Platform comparison" });
    expect(within(table).getByText("321")).toBeInTheDocument(); // unique viewers column
    expect(screen.getByText("Not connected — no data")).toBeInTheDocument(); // YouTube
    expect(screen.getAllByText("বিয়ের কার্ডে দেরি? নিজেই বানান নিমন্ত্রণ।").length).toBeGreaterThan(0);
  });

  it("explains posts the platform returned no metrics for, instead of 'awaiting sync'", () => {
    const noData = {
      ...POST,
      schedule_id: "s-nd",
      has_metrics: false,
      views_in_period: null,
      lifetime_views: null,
      metrics_status: "NO_DATA" as const,
      metrics_detail: "The platform returned no metrics for this post.",
    };
    const r = report(1234);
    r.coverage.no_data_posts = 1;
    render(
      <BrandProvider brands={BRANDS}>
        <AnalyticsView
          initialViews={{ path: viewsPath(BRAND, "30d"), data: r }}
          initialPosts={{
            path: postsPath(BRAND, "30d", "views", 1, {}, 10),
            data: { ...POSTS, total: 2, items: [POST, noData] },
          }}
          initialPipeline={{ path: pipelinePath(BRAND), data: PIPELINE }}
          initialAccount={{ path: accountPath(BRAND), data: ACCOUNT }}
        />
      </BrandProvider>,
    );
    expect(screen.getByText("No data from platform")).toBeInTheDocument();
    expect(screen.getByText(/returned no\s+metrics from the platform/)).toBeInTheDocument();
  });

  it("refetches views and posts for the selected period", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) =>
        String(input).includes("/analytics/posts") ? json(POSTS) : json(report(77, "7d")),
      );
    renderView();
    await user.click(screen.getByRole("radio", { name: "7 days" }));
    await waitFor(() => expect(screen.getAllByText("77").length).toBeGreaterThan(0));
    const urls = fetchSpy.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/api/v1/analytics/views") && u.includes("range=7d"))).toBe(true);
    expect(urls.some((u) => u.includes("/api/v1/analytics/posts") && u.includes("range=7d"))).toBe(true);
  });

  it("opens per-platform post analytics in a dialog", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({
        content_package_id: "pkg-1",
        title: POST.title,
        format: "CAROUSEL",
        tags: { hook_type: "PAIN_POINT" },
        thumbnail_asset_id: null,
        range: "30d",
        window: { start: "", end: "" },
        granularity: "day",
        aggregate: { lifetime_views: 1500, views_in_period: 1234, platforms: 1 },
        platforms: [
          {
            ...POST,
            view_definition: "Instagram views",
            metrics: [{ key: "views", label: "Views", lifetime: 1500, in_period: 1234, approximate: false }],
            trend: [{ date: "2026-11-16", views: 1234 }],
            history: [],
          },
        ],
        sync: SYNC,
      }),
    );
    renderView();
    const rows = screen.getAllByRole("button", { name: new RegExp(POST.title.slice(0, 6)) });
    await user.click(rows[rows.length - 1]);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getAllByText("1,500").length).toBeGreaterThan(0));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Whole-account totals", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows account-wide numbers per platform, including posts made outside Media OS", () => {
    vi.spyOn(globalThis, "fetch");
    renderView();
    const card = screen.getByRole("region", { name: "Whole account" });
    expect(within(card).getByText(/including ones posted directly/i)).toBeInTheDocument();
    expect(within(card).getByText("4,321")).toBeInTheDocument();
    expect(within(card).getByText("Nimontron IG")).toBeInTheDocument();
    expect(within(card).getByText(/Instagram can run up to 2 days behind/)).toBeInTheDocument();
  });
});
