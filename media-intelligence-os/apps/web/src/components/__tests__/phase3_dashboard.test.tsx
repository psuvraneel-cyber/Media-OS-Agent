import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { replaceMock, refreshMock, redirectMock, cookiesMock } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  refreshMock: vi.fn(),
  redirectMock: vi.fn((target: string) => {
    throw new Error(`NEXT_REDIRECT:${target}`);
  }),
  cookiesMock: vi.fn(async () => ({
    toString: (): string => "",
  })),
}));

let mockPathname = "/dashboard";
let mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, refresh: refreshMock }),
  usePathname: () => mockPathname,
  useSearchParams: () => mockSearchParams,
  redirect: redirectMock,
}));

vi.mock("next/headers", () => ({
  cookies: cookiesMock,
}));

import AuthenticatedShellLayout from "@/app/(shell)/layout";
import { OperatorShell } from "@/components/shell/OperatorShell";
import { DashboardOverviewView } from "@/components/views/DashboardOverviewView";
import { LoginForm } from "@/components/views/LoginForm";
import { IntegrationsView, LogsView, SettingsView } from "@/components/views/OperationsViews";
import {
  AgentsView,
  AnalyticsView,
  AssetsView,
  CalendarView,
  StudioView,
} from "@/components/views/PipelineStubViews";
import { BrandProvider } from "@/lib/brand-context";
import { NAV_ROUTES } from "@/lib/navigation";
import type {
  AuditLogResponse,
  BrandProfileSummary,
  ChannelAccountSummary,
  SessionUser,
  SystemStatusResponse,
  TelemetryLogResponse,
} from "@/lib/types";

const BRAND_TECH_ID = "88888888-1111-4111-8111-111111111111";
const BRAND_APP_ID = "88888888-2222-4222-8222-222222222222";

const BRAND_FIXTURES: BrandProfileSummary[] = [
  {
    brand_id: BRAND_TECH_ID,
    slug: "brand-tech-ai",
    name: "Technology & AI",
    niche: "AI and developer intelligence.",
    description: "Primary technical editorial brand.",
    target_audience: "Developers, AI enthusiasts and software engineers.",
    language: "en",
    tone_of_voice: "Technically credible, informative, concise and analytical.",
    content_pillars: ["AI models and releases", "AI tools", "Open-source projects"],
    visual_identity: { configured: true },
    timezone: "Asia/Kolkata",
    approval_policy: "human_required",
    placeholder_unconfigured: false,
    active: true,
    data_origin: "postgresql",
  },
  {
    brand_id: BRAND_APP_ID,
    slug: "brand-app-promo",
    name: "App Promotion",
    niche: "App promotion and product education.",
    description: "Unconfigured app identity placeholder.",
    target_audience: "Provisional app users (unconfigured).",
    language: "en",
    tone_of_voice: "Credible, clear and product-focused.",
    content_pillars: ["Product education", "Release updates"],
    visual_identity: { configured: false },
    timezone: "Asia/Kolkata",
    approval_policy: "human_required",
    placeholder_unconfigured: true,
    active: true,
    data_origin: "postgresql",
  },
];

const EMPTY_CHANNELS: ChannelAccountSummary[] = [];

const SESSION_USER: SessionUser = {
  authenticated: true,
  username: "local-owner",
  role: "owner",
  csrf_token: "csrf-token-fixture-123",
  expires_at_utc: "2026-11-11T00:00:00+00:00",
  phase: "phase-4-persistence",
};

const SYSTEM_STATUS: SystemStatusResponse = {
  service: "media-os-api",
  phase: "phase-4-persistence",
  environment: "test",
  display_timezone: "Asia/Kolkata",
  auth_mode: "single_owner_postgresql_cookie",
  persistent_storage: true,
  database_status: "ready",
  redis_status: "ready",
  schema_version: "0003_domain_integrity_hardening",
  csrf_protection: true,
  postiz: {
    status: "reachable",
    checked_at_utc: "2026-11-10T12:00:00+00:00",
    detail: "Standalone Postiz HTTP gateway responded cleanly.",
    api_credentials_managed_in_phase: "phase-8",
  },
  modules: [
    {
      module_id: "postgres-redis-celery",
      title: "PostgreSQL, Redis, Celery Worker & Scheduler",
      status: "active_phase_4",
      target_phase: "phase-4",
      summary: "PostgreSQL 17, Redis 7.2, Celery JSON worker, and persistent scheduler active.",
    },
    {
      module_id: "reddit-source-policy",
      title: "Reddit Connector Policy Gate",
      status: "blocked_by_policy",
      target_phase: "blocked",
      summary: "Explicitly BLOCKED pending Data API Terms review.",
    },
  ],
  timestamp_utc: "2026-11-10T12:00:00+00:00",
};

const TELEMETRY_LOGS: TelemetryLogResponse = {
  ephemeral: true,
  persistent: false,
  note: "Ephemeral in-memory trace.",
  items: [
    {
      timestamp_utc: "2026-11-10T12:00:00+00:00",
      request_id: "req-1234567890ab",
      method: "GET",
      route: "/api/v1/system/status",
      status_code: 200,
      duration_ms: 4.2,
      error_category: null,
    },
  ],
};

const AUDIT_EVENTS: AuditLogResponse = {
  persistent: true,
  items: [
    {
      id: "audit-1",
      timestamp_utc: "2026-11-10T12:01:00+00:00",
      actor_id: "owner:local-owner",
      action: "BRAND_ACTIVATION_CHANGED",
      target_type: "BrandProfile",
      target_id: BRAND_APP_ID,
      request_id: "req-audit-1",
      outcome: "SUCCESS",
      metadata: { slug: "brand-app-promo", is_active: true },
    },
  ],
};

function renderInShell(
  node: React.ReactNode,
  initialBrandId?: string,
  brands: BrandProfileSummary[] = BRAND_FIXTURES,
) {
  return render(
    <BrandProvider brands={brands} initialBrandId={initialBrandId}>
      <OperatorShell sessionUser={SESSION_USER}>{node}</OperatorShell>
    </BrandProvider>,
  );
}

describe("Media Intelligence OS Dashboard (Phase 4 Persistence)", () => {
  beforeEach(() => {
    mockPathname = "/dashboard";
    mockSearchParams = new URLSearchParams();
    replaceMock.mockReset();
    refreshMock.mockReset();
    redirectMock.mockClear();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("defines and renders all 12 navigation routes with active route semantics and responsive menu", async () => {
    expect(NAV_ROUTES).toHaveLength(12);
    const user = userEvent.setup();

    renderInShell(<DashboardOverviewView systemStatus={SYSTEM_STATUS} />);

    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL + Redis")).toBeInTheDocument();

    const primaryNav = screen.getByRole("navigation", { name: "Primary sections" });
    const overviewLink = within(primaryNav).getByRole("link", { name: /Overview/i });
    expect(overviewLink).toHaveAttribute("aria-current", "page");

    for (const route of NAV_ROUTES) {
      expect(
        within(primaryNav).getByRole("link", { name: new RegExp(route.label, "i") }),
      ).toBeInTheDocument();
    }

    await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
    expect(screen.getByRole("button", { name: "Close navigation menu" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close navigation menu" }));
    expect(screen.queryByRole("button", { name: "Close navigation menu" })).not.toBeInTheDocument();
  });

  it("isolates brand context and shows honest empty channel state across Technology & AI and App Promotion", async () => {
    const user = userEvent.setup();
    renderInShell(
      <IntegrationsView systemStatus={SYSTEM_STATUS} channels={EMPTY_CHANNELS} />,
    );

    expect(
      screen.getByText("No connected social channels for Technology & AI"),
    ).toBeInTheDocument();

    const brandSelect = screen.getByLabelText("Active Brand Profile");
    await user.selectOptions(brandSelect, BRAND_APP_ID);

    expect(
      screen.getByText("No connected social channels for App Promotion"),
    ).toBeInTheDocument();
    expect(screen.getByText(/App name, logo & features unconfigured/i)).toBeInTheDocument();
    expect(replaceMock).toHaveBeenCalledWith(`/dashboard?brand=${BRAND_APP_ID}`, {
      scroll: false,
    });
  });

  it("keeps deactivated brands selectable in BrandSwitcher and allows reactivation", async () => {
    const user = userEvent.setup();
    const withInactive = [
      BRAND_FIXTURES[0],
      { ...BRAND_FIXTURES[1], active: false },
    ];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ ...BRAND_FIXTURES[1], active: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    renderInShell(
      <SettingsView sessionUser={SESSION_USER} systemStatus={SYSTEM_STATUS} />,
      BRAND_APP_ID,
      withInactive,
    );

    expect(screen.getByRole("option", { name: /App Promotion.*\[Inactive\]/i })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Activate Brand$/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
    const [actUrl, actInit] = fetchSpy.mock.calls[0];
    expect(actUrl).toBe(`/api/v1/brands/${BRAND_APP_ID}/activation`);
    expect(actInit?.body).toBe(JSON.stringify({ is_active: true }));
  });

  it("renders all non-research domain views honestly with empty/disabled/policy states, persisted audit logs, and zero fake analytics", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(TELEMETRY_LOGS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(AUDIT_EVENTS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    const { unmount } = renderInShell(
      <>
        <DashboardOverviewView systemStatus={SYSTEM_STATUS} />
        <StudioView />
        <AssetsView />
        <CalendarView />
        <AnalyticsView />
        <AgentsView />
        <IntegrationsView systemStatus={SYSTEM_STATUS} channels={EMPTY_CHANNELS} />
        <SettingsView sessionUser={SESSION_USER} systemStatus={SYSTEM_STATUS} />
        <LogsView initialLogs={TELEMETRY_LOGS} initialAuditEvents={AUDIT_EVENTS} />
      </>,
    );

    expect(screen.getByText("No performance records collected for Technology & AI")).toBeInTheDocument();
    expect(screen.getByText(/App Promotion/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate with Gemini/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Queue in Postiz/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Bind Postiz Channel/i })).toBeDisabled();
    expect(screen.getByText("GET /api/v1/system/status")).toBeInTheDocument();
    expect(screen.getByText("BRAND_ACTIVATION_CHANGED")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Refresh Logs/i }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    });
    expect(fetchSpy.mock.calls.map((c) => c[0])).toEqual([
      "/api/v1/system/logs?limit=40",
      "/api/v1/system/audit-events?limit=40",
    ]);
    unmount();
  });

  it("redirects unauthenticated SSR requests to /login and never writes tokens to localStorage on login", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: { code: "UNAUTHORIZED", message: "Authentication required.", request_id: "r1" },
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      AuthenticatedShellLayout({ children: <div>Protected</div> }),
    ).rejects.toThrow("NEXT_REDIRECT:/login");

    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: {
            code: "RATE_LIMITED",
            message: "Too many failed attempts.",
            request_id: "r2",
          },
        }),
        { status: 429, headers: { "Content-Type": "application/json", "Retry-After": "60" } },
      ),
    );

    render(<LoginForm />);
    await user.type(screen.getByLabelText("Owner Username"), "local-owner");
    await user.type(screen.getByLabelText("Password"), "bad-pass-123");
    await user.click(screen.getByRole("button", { name: /Sign in to Control Plane/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Too many failed attempts/i);
    });
    expect(window.localStorage.length).toBe(0);
  });

  it("sends X-CSRF-Token when signing out or toggling brand activation", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...BRAND_FIXTURES[0], active: false }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ authenticated: false, message: "Signed out cleanly." }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    renderInShell(<SettingsView sessionUser={SESSION_USER} systemStatus={SYSTEM_STATUS} />);
    await user.click(screen.getByRole("button", { name: /Deactivate Brand/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
    const [actUrl, actInit] = fetchSpy.mock.calls[0];
    expect(actUrl).toBe(`/api/v1/brands/${BRAND_TECH_ID}/activation`);
    expect((actInit?.headers as Headers).get("X-CSRF-Token")).toBe("csrf-token-fixture-123");
    expect(refreshMock).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /Sign out/i }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();

    const confirmBtn = screen
      .getAllByRole("button", { name: "Sign out" })
      .find((btn) => dialog.contains(btn));
    expect(confirmBtn).toBeDefined();
    await user.click(confirmBtn!);

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    });
    const [url, init] = fetchSpy.mock.calls[1];
    expect(url).toBe("/api/v1/auth/logout");
    expect((init?.headers as Headers).get("X-CSRF-Token")).toBe("csrf-token-fixture-123");
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });
});
