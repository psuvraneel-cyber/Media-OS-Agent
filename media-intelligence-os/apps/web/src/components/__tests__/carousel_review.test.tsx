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
  schedules: [],
  scheduling: false,
  can_schedule: false,
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

  it("explains a QA-failed draft and offers a re-check instead of 'still rendering'", async () => {
    const failed = {
      ...DETAIL, status: "DRAFTED", qa_passed: false, assets: [],
      qa_report: { passed: false, checks: [{ name: "banned_phrases", status: "FAIL", detail: "x" }] },
    };
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push(`${(init?.method ?? "GET").toUpperCase()} ${url}`);
      if (url.includes("/recheck")) return json({ ...SUMMARY, status: "DRAFTED", qa_passed: true });
      if (url.includes("/content/carousels/c1")) return json(failed);
      return json({ items: [{ ...SUMMARY, status: "DRAFTED", qa_passed: false }] });
    });
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <CarouselReviewView />
      </BrandProvider>,
    );
    expect(await screen.findByText(/didn.t pass the automated checks/)).toBeInTheDocument();
    expect(screen.queryByText(/Slides are rendering/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Re-check with latest rules/ }));
    expect(calls.some((c) => c.startsWith("POST") && c.includes("/recheck"))).toBe(true);
  });

  it("edits slide text and sends only what changed", async () => {
    const withSlides = {
      ...DETAIL,
      slides: [
        { role: "HOOK", kicker: null, headline_bn: "বিয়ের মরশুম", headline_en: "Wedding season", body_bn: null, body_en: null },
        { role: "CTA", kicker: null, headline_bn: "প্লে থেকে নিন", headline_en: "Get it on Play", body_bn: null, body_en: null },
      ],
    };
    const calls: { url: string; method: string; body?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method, body: init?.body as string | undefined });
      if (url.includes("/content/carousels/c1")) return json(withSlides);
      return json({ items: [SUMMARY] });
    });
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <CarouselReviewView />
      </BrandProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /Edit text/ }));
    const headline = screen.getAllByLabelText("Headline (English)")[0];
    await userEvent.clear(headline);
    await userEvent.type(headline, "Wedding *season* is here");
    await userEvent.click(screen.getByRole("button", { name: /Save and re-check/ }));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toContain(`/content/carousels/c1?brand=${APP}`);
    const body = JSON.parse(patch?.body ?? "{}");
    expect(body.slides).toEqual([{ headline_en: "Wedding *season* is here" }, {}]);
    expect(body.hashtags).toEqual(["#Nimontron"]);
  });

  it("flags missing slide images, blocks approval and re-renders", async () => {
    const broken = { ...DETAIL, slides_missing: 6, assets: [{ id: "a1", index: 1, width: 1080, height: 1350 }] };
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push(`${(init?.method ?? "GET").toUpperCase()} ${url}`);
      if (url.includes("/rerender")) return json({ ...SUMMARY, status: "DRAFTED" });
      if (url.includes("/content/carousels/c1")) return json(broken);
      return json({ items: [{ ...SUMMARY, slides_missing: 6 }] });
    });
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <CarouselReviewView />
      </BrandProvider>,
    );
    expect(await screen.findByText(/slide images are missing or empty/)).toBeInTheDocument();
    expect(screen.getByText("Slides missing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Re-render slides/ }));
    expect(calls.some((c) => c.startsWith("POST") && c.includes("/content/carousels/c1/rerender"))).toBe(true);
  });

  it("explains a failed render check and refreshes with feedback", async () => {
    const failed = {
      ...DETAIL, status: "RENDERED", qa_passed: false,
      qa_report: { passed: false, checks: [{ name: "render_text_fits", status: "FAIL", detail: "slide 6: text box 2 too wide" }] },
    };
    let lists = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/content/carousels/c1")) return json(failed);
      lists += 1;
      return json({ items: [{ ...SUMMARY, status: "RENDERED", qa_passed: false }] });
    });
    render(
      <BrandProvider brands={[BRAND]} initialBrandId={APP}>
        <CarouselReviewView />
      </BrandProvider>,
    );
    expect(await screen.findByText(/slide 6: text box 2 too wide/, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("Render check failed")).toBeInTheDocument();
    const before = lists;
    await userEvent.click(screen.getByRole("button", { name: /^Refresh$/ }));
    await screen.findByRole("button", { name: /^Refresh$/ });
    expect(lists).toBeGreaterThan(before);
  });

  it("disables approve for carousels that have not passed QA", async () => {
    const draft = { ...DETAIL, status: "DRAFTED", qa_passed: true, assets: [] };
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
    expect(await screen.findByText(/Slides are rendering/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Reject/ })).toBeEnabled();
  });
});
