import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/research",
  useSearchParams: () => new URLSearchParams(),
}));

import { ResearchView, TopicsView } from "@/components/views/ResearchViews";
import { BrandProvider } from "@/lib/brand-context";
import type {
  BrandProfileSummary,
  ResearchItemListResponse,
  ResearchSourceListResponse,
  TopicDetailResponse,
  TopicListResponse,
} from "@/lib/types";

const TECH = "88888888-1111-4111-8111-111111111111";
const APP = "88888888-2222-4222-8222-222222222222";

function brand(id: string, slug: string, name: string): BrandProfileSummary {
  return {
    brand_id: id,
    slug,
    name,
    niche: "n",
    description: "d",
    target_audience: "a",
    language: "en",
    tone_of_voice: "t",
    content_pillars: [],
    visual_identity: {},
    timezone: "Asia/Kolkata",
    approval_policy: "human_required",
    placeholder_unconfigured: false,
    active: true,
    data_origin: "postgresql",
  };
}

const BRANDS = [brand(TECH, "brand-tech-ai", "Technology & AI"), brand(APP, "brand-app-promo", "App Promotion")];

const SOURCES: ResearchSourceListResponse = {
  live_fetch_kill_switch_enabled: false,
  items: [
    {
      source: "HACKERNEWS",
      display_name: "Hacker News (official Firebase API)",
      policy_mode: "OFF",
      live_fetch_approved: false,
      approved_at_utc: null,
      health_status: "DISABLED",
      consecutive_failures: 0,
      last_attempt_at_utc: null,
      last_success_at_utc: null,
      last_failure_at_utc: null,
      last_error_code: null,
      backoff_until_utc: null,
      last_run_totals: {},
      schedule: { enabled: false, next_run_at_utc: null, cadence: { kind: "interval", seconds: 3600 } },
      checkpoints: [],
    },
    {
      source: "REDDIT",
      display_name: "Reddit (BLOCKED by policy SRC-01)",
      policy_mode: "BLOCKED",
      live_fetch_approved: false,
      approved_at_utc: null,
      health_status: "BLOCKED",
      consecutive_failures: 0,
      last_attempt_at_utc: null,
      last_success_at_utc: null,
      last_failure_at_utc: null,
      last_error_code: null,
      backoff_until_utc: null,
      last_run_totals: {},
      schedule: null,
      checkpoints: [],
    },
  ],
};

function item(id: string, url: string): ResearchItemListResponse["items"][number] {
  return {
    source_item_id: id,
    provider: "ARXIV_PAPER",
    external_id: `2611.0000${id}v1`,
    title: `Paper ${id}`,
    source_label: "arXiv Paper",
    canonical_url: url,
    published_at_utc: "2026-11-12T10:00:00+00:00",
    fetched_at_utc: "2026-11-13T10:00:00+00:00",
    summary: "A short summary.",
    source_policy_mode: "EXPERIMENTAL",
    clusters: [{ topic_cluster_id: "c1", dedupe_key: "arxiv:paper:2611.00001", discovery_status: "DISCOVERED" }],
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function topics(brandId: string, status: string): TopicListResponse {
  return {
    brand_id: brandId,
    total: 1,
    items: [
      {
        topic_cluster_id: "11111111-1111-4111-8111-111111111111",
        title: "Shared paper topic",
        category: "AI Research",
        dedupe_key: "arxiv:paper:2611.00001",
        discovery_status: "DISCOVERED",
        first_seen_at_utc: "2026-11-13T10:00:00+00:00",
        last_seen_at_utc: "2026-11-13T11:00:00+00:00",
        source_count: 2,
        providers: ["ARXIV_PAPER"],
        assignment: {
          brand_id: brandId,
          editorial_status: status,
          relevance_score: null,
          priority: 0,
          assigned_at_utc: "2026-11-13T10:00:00+00:00",
          evaluated_at_utc: null,
        },
      },
    ],
  };
}

describe("Research and Topics views (Phase 5, M6)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows sources with policy, kill switch and safe external links, and paginates items", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(SOURCES))
      .mockResolvedValueOnce(
        json({
          total: 3,
          next_before_utc: "2026-11-13T10:00:00+00:00",
          items: [item("1", "https://arxiv.org/abs/2611.00001v1"), item("2", "javascript:alert(1)")],
        }),
      )
      .mockResolvedValueOnce(json({ total: 3, next_before_utc: null, items: [item("3", "https://arxiv.org/abs/x")] }));

    render(
      <BrandProvider brands={BRANDS}>
        <ResearchView />
      </BrandProvider>,
    );

    await screen.findByTestId("source-REDDIT");
    expect(within(screen.getByTestId("source-REDDIT")).getAllByText("BLOCKED").length).toBe(2);
    expect(within(screen.getByTestId("source-HACKERNEWS")).getByText("OFF")).toBeInTheDocument();
    expect(screen.getByText("Kill switch: off")).toBeInTheDocument();
    expect(fetchSpy.mock.calls.map((c) => c[0])).toEqual([
      "/api/v1/research/sources",
      "/api/v1/research/items?limit=20",
    ]);

    const safe = screen.getByText("Paper 1").closest("a");
    expect(safe).toHaveAttribute("href", "https://arxiv.org/abs/2611.00001v1");
    expect(safe).toHaveAttribute("rel", "noopener noreferrer nofollow");
    expect(screen.getByText("Paper 2").closest("a")).toBeNull(); // non-http URL is not a link

    await user.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("Paper 3");
    expect(fetchSpy.mock.calls[2][0]).toBe(
      "/api/v1/research/items?limit=20&before=2026-11-13T10%3A00%3A00%2B00%3A00",
    );
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("renders an honest empty state and surfaces API errors", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(SOURCES))
      .mockResolvedValueOnce(json({ total: 0, next_before_utc: null, items: [] }));
    const { unmount } = render(
      <BrandProvider brands={BRANDS}>
        <ResearchView />
      </BrandProvider>,
    );
    expect(await screen.findByText("No discovered items yet")).toBeInTheDocument();
    unmount();

    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      json({ error: { code: "DATABASE_UNAVAILABLE", message: "down", request_id: "r1" } }, 503),
    );
    render(
      <BrandProvider brands={BRANDS}>
        <ResearchView />
      </BrandProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("DATABASE_UNAVAILABLE");
  });

  it("requests topics for the active brand only, filters by status and opens provenance", async () => {
    const user = userEvent.setup();
    const detail: TopicDetailResponse = {
      topic: topics(APP, "PENDING_EVALUATION").items[0],
      sources: [item("1", "https://arxiv.org/abs/2611.00001v1"), item("2", "https://arxiv.org/abs/2611.00001v2")],
    };
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(topics(APP, "PENDING_EVALUATION")))
      .mockResolvedValueOnce(json({ brand_id: APP, total: 0, items: [] }))
      .mockResolvedValueOnce(json(topics(APP, "PENDING_EVALUATION")))
      .mockResolvedValueOnce(json(detail));

    render(
      <BrandProvider brands={BRANDS} initialBrandId={APP}>
        <TopicsView />
      </BrandProvider>,
    );

    await screen.findByText("Shared paper topic");
    expect(screen.getByText("PENDING_EVALUATION", { selector: "span" })).toBeInTheDocument();
    expect(fetchSpy.mock.calls[0][0]).toBe(`/api/v1/topics?brand=${APP}&limit=50`);

    await user.click(screen.getByRole("button", { name: "Editorial status filter" }));
    await user.click(screen.getByRole("option", { name: "Rejected" }));
    await screen.findByText("No topics for App Promotion");
    expect(fetchSpy.mock.calls[1][0]).toBe(`/api/v1/topics?brand=${APP}&limit=50&editorial_status=REJECTED`);

    await user.click(screen.getByRole("button", { name: "Editorial status filter" }));
    await user.click(screen.getByRole("option", { name: "All" }));
    await user.click(await screen.findByText("Shared paper topic"));
    await screen.findByText("Provenance (2)");
    expect(fetchSpy.mock.calls[3][0]).toBe(
      `/api/v1/topics/11111111-1111-4111-8111-111111111111?brand=${encodeURIComponent(APP)}`,
    );
    for (const call of fetchSpy.mock.calls) {
      expect(String(call[0])).not.toContain(TECH); // never asks for the other brand's decisions
    }
  });
});
