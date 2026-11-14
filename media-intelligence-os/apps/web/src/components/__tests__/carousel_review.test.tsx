import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/approvals",
  useSearchParams: () => new URLSearchParams(),
}));

import { CarouselReviewView } from "@/components/views/CarouselReviewView";
import { BrandProvider } from "@/lib/brand-context";
import type { BrandProfileSummary, CarouselDetail, CarouselSummary } from "@/lib/types";

const APP = "88888888-2222-4222-8222-222222222222";

const BRAND: BrandProfileSummary = {
  brand_id: APP,
  slug: "brand-app-promo",
  name: "Nimontron",
  niche: "n",
  description: "d",
  target_audience: "a",
  language: "bn",
  tone_of_voice: "t",
  content_pillars: [],
  visual_identity: {},
  timezone: "Asia/Kolkata",
  approval_policy: "human_required",
  placeholder_unconfigured: false,
  active: true,
  data_origin: "postgresql",
};

const SUMMARY: CarouselSummary = {
  id: "c1",
  status: "QA_PASSED",
  approval_status: "PENDING",
  title: "Wedding season is here",
  hook: "বিয়ের মরশুম এসে গেল",
  language: "BN_EN",
  pillar: "wedding_season",
  hook_type: "SEASONAL",
  slide_count: 5,
  qa_passed: true,
  qa_warnings: 0,
  score_total: 84,
  score_label: "Heuristic v0 — rubric estimate, not a reach prediction",
  thumbnail_asset_id: "a1",
  created_at: "2026-11-14T10:00:00Z",
};

const DETAIL: CarouselDetail = {
  ...SUMMARY,
  caption: "বিয়ের কার্ড\n\nBengali wedding cards",
  hashtags: ["#Nimontron"],
  alt_text: "Five slides",
  slides: [],
  assets: [{ id: "a1", index: 1, width: 1080, height: 1350 }],
  qa_report: { passed: true, checks: [{ name: "claims_approved", status: "PASS", detail: "" }] },
  score: { total: 84, label: SUMMARY.score_label ?? "", suggestion: "Warmer hook" },
  post_tags: {},
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

describe("CarouselReviewView", () => {
  it("lists queued carousels, shows slides, QA and the heuristic label, and approves", async () => {
    const calls: { url: string; method: string; body?: string }[] = [];
    let decided = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method, body: init?.body as string | undefined });
      if (url.includes("/decision")) {
        decided = true;
        return json({ ...SUMMARY, approval_status: "APPROVED" });
      }
      if (url.includes("/content/carousels/c1")) return json(DETAIL);
      return json({ items: decided ? [] : [SUMMARY] });
    });

    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <CarouselReviewView />
      </BrandProvider>,
    );

    expect(await screen.findByAltText("Slide 1")).toHaveAttribute(
      "src",
      `/api/v1/content/assets/a1/file?brand=${APP}`,
    );
    expect(screen.getByText(/rubric estimate, not a reach prediction/)).toBeInTheDocument();
    expect(screen.getByText("claims_approved")).toBeInTheDocument();
    expect(screen.getByText("Bilingual")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Approve/ }));
    const decision = calls.find((c) => c.url.includes("/decision"));
    expect(decision?.method).toBe("POST");
    expect(JSON.parse(decision?.body ?? "{}")).toEqual({ decision: "APPROVE", reason: null });
    expect(await screen.findByText(/0 carousels awaiting review/)).toBeInTheDocument();
  });

  it("disables approve for carousels that have not passed QA", async () => {
    const draft = { ...DETAIL, status: "DRAFTED", assets: [] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/content/carousels/c1")) return json(draft);
      return json({ items: [{ ...SUMMARY, status: "DRAFTED" }] });
    });
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <CarouselReviewView />
      </BrandProvider>,
    );
    expect(await screen.findByText(/still rendering/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Reject/ })).toBeEnabled();
  });
});
