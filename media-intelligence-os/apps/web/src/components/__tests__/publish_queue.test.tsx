import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/approvals",
  useSearchParams: () => new URLSearchParams(),
}));

import { PublishQueueView, defaultSlot } from "@/components/views/PublishQueueView";
import { BrandProvider } from "@/lib/brand-context";
import type { BrandProfileSummary, CarouselSummary } from "@/lib/types";

const APP = "88888888-2222-4222-8222-222222222222";
const BRAND: BrandProfileSummary = {
  brand_id: APP, slug: "brand-app-promo", name: "Nimontron", niche: "n", description: "d",
  target_audience: "a", language: "bn", tone_of_voice: "t", content_pillars: [],
  visual_identity: {}, timezone: "Asia/Kolkata", approval_policy: "human_required",
  placeholder_unconfigured: false, active: true, data_origin: "postgresql",
};

const APPROVED: CarouselSummary = {
  id: "c1", status: "QA_PASSED", approval_status: "APPROVED", title: "t",
  hook: "বিয়ের কার্ডে ভুল বানান?", language: "BN_EN", pillar: "wedding_season",
  hook_type: "PAIN_POINT", slide_count: 6, qa_passed: true, qa_warnings: 0, score_total: 79,
  score_label: "Heuristic v0", thumbnail_asset_id: "a1", created_at: "2026-11-14T10:00:00Z",
  schedules: [], scheduling: false, can_schedule: true,
};

const SCHEDULED: CarouselSummary = {
  ...APPROVED, id: "c2", status: "PUBLISHED", hook: "Did you spell everyone's name right?",
  can_schedule: false,
  schedules: [{ platform: "INSTAGRAM", status: "PUBLISHED", scheduled_at: "2026-11-18T13:30:00Z",
                timezone: "Asia/Kolkata", post_url: "https://www.instagram.com/p/abc/" }],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

const CANCELLED: CarouselSummary = {
  ...APPROVED, id: "c3", status: "FAILED_RETRYABLE", hook: "Cancelled in Postiz",
  can_schedule: true,
  schedules: [{ platform: "INSTAGRAM", status: "CANCELLED", scheduled_at: "2026-11-16T13:30:00Z",
                timezone: "Asia/Kolkata", post_url: null }],
};

describe("PublishQueueView", () => {
  it("offers to reschedule a carousel whose earlier post was cancelled", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ items: [CANCELLED] }));
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <PublishQueueView />
      </BrandProvider>,
    );
    expect(await screen.findByText(/earlier post failed or was cancelled/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reschedule" })).toBeInTheDocument();
  });

  it("reschedules a carousel whose posts failed on every platform", async () => {
    const FAILED: CarouselSummary = {
      ...CANCELLED, id: "c4", hook: "Failed on Meta",
      schedules: [
        { platform: "FACEBOOK", status: "FAILED", scheduled_at: "2026-11-15T13:30:00Z",
          timezone: "Asia/Kolkata", post_url: null },
        { platform: "INSTAGRAM", status: "FAILED", scheduled_at: "2026-11-15T13:30:00Z",
          timezone: "Asia/Kolkata", post_url: null },
      ],
    };
    const posts: { url: string; body: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if ((init?.method ?? "GET").toUpperCase() === "POST") {
        posts.push({ url, body: init?.body as string });
        return json({ ...FAILED, scheduling: true, can_schedule: false });
      }
      return json({ items: [FAILED] });
    });
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <PublishQueueView />
      </BrandProvider>,
    );
    const button = await screen.findByRole("button", { name: "Reschedule" });
    expect(screen.getAllByText("FAILED")).toHaveLength(2);
    await userEvent.click(button);
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toContain("/content/carousels/c4/schedule");
    expect(JSON.parse(posts[0].body).platforms).toEqual(["INSTAGRAM", "FACEBOOK"]);
  });

  it("labels the button Schedule (not Reschedule) for a first-time post", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ items: [APPROVED] }));
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <PublishQueueView />
      </BrandProvider>,
    );
    expect(await screen.findByRole("button", { name: "Schedule" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reschedule" })).not.toBeInTheDocument();
  });

  it("schedules an approved carousel to the chosen platforms and shows published links", async () => {
    const calls: { url: string; method: string; body?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push({ url, method: (init?.method ?? "GET").toUpperCase(), body: init?.body as string });
      if (url.includes("/schedule")) return json({ ...APPROVED, scheduling: true });
      return json({ items: [APPROVED, SCHEDULED] });
    });
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <PublishQueueView />
      </BrandProvider>,
    );
    expect(await screen.findByText("বিয়ের কার্ডে ভুল বানান?", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View post/ })).toHaveAttribute(
      "href", "https://www.instagram.com/p/abc/",
    );
    // Only the unscheduled carousel offers a schedule form.
    expect(screen.getAllByRole("button", { name: /Schedule/ })).toHaveLength(1);
    await userEvent.click(screen.getByRole("checkbox", { name: "Facebook" }));
    await userEvent.click(screen.getByRole("button", { name: /Schedule/ }));
    const post = calls.find((c) => c.url.includes("/schedule"));
    expect(post?.method).toBe("POST");
    const body = JSON.parse(post?.body ?? "{}");
    expect(body.platforms).toEqual(["INSTAGRAM"]);
    expect(new Date(body.scheduled_at).toISOString()).toBe(body.scheduled_at);
  });

  it("defaults to the next 19:00 slot at least an hour away", () => {
    expect(defaultSlot(new Date(2026, 10, 18, 10, 0))).toBe("2026-11-18T19:00");
    expect(defaultSlot(new Date(2026, 10, 18, 18, 30))).toBe("2026-11-19T19:00");
  });
});
