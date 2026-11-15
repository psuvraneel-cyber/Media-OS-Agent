import React from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/calendar",
  useSearchParams: () => new URLSearchParams(),
}));

import { CalendarView } from "@/components/views/CalendarView";
import { BrandProvider } from "@/lib/brand-context";
import type { BrandProfileSummary } from "@/lib/types";

const BRAND = "99999999-1111-4111-8111-111111111111";
const BRANDS = [
  {
    brand_id: BRAND,
    slug: "brand-app-promo",
    name: "Nimontron",
    niche: "n",
    description: "d",
    active: true,
    data_origin: "postgresql",
    placeholder_unconfigured: false,
    content_pillars: [],
  } as unknown as BrandProfileSummary,
];

function istToday(hour: number): string {
  const key = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  return new Date(`${key}T${String(hour).padStart(2, "0")}:00:00+05:30`).toISOString();
}

afterEach(() => vi.restoreAllMocks());

describe("CalendarView", () => {
  it("loads this month's posts, flags overdue ones and links to Postiz", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          start: "",
          end: "",
          items: [
            {
              schedule_id: "s1",
              content_package_id: "p1",
              title: "বিয়ের কার্ডে দেরি?",
              platform: "INSTAGRAM",
              status: "OVERDUE",
              overdue: true,
              scheduled_at: istToday(0),
              post_url: null,
              thumbnail_asset_id: null,
            },
            {
              schedule_id: "s2",
              content_package_id: "p2",
              title: "Published one",
              platform: "FACEBOOK",
              status: "PUBLISHED",
              overdue: false,
              scheduled_at: istToday(1),
              post_url: "https://facebook.com/x",
              thumbnail_asset_id: null,
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(
      <BrandProvider brands={BRANDS}>
        <CalendarView postizUrl="http://localhost:4007" />
      </BrandProvider>,
    );
    expect(await screen.findByText(/1 post is overdue/)).toBeInTheDocument();
    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("/api/v1/content/schedules?");
    expect(url).toContain(`brand=${BRAND}`);
    expect(screen.getAllByText("বিয়ের কার্ডে দেরি?").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Open Postiz/ })).toHaveAttribute("href", "http://localhost:4007");
  });
});
