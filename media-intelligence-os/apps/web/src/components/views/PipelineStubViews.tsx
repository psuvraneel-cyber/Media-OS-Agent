"use client";

import React, { useState } from "react";
import {
  Clapperboard,
  Cpu,
  FolderKanban,
  MonitorSmartphone,
  Palette,
  Plug,
  Radar,
  Server,
  Timer,
} from "lucide-react";

import { Badge, StatusDot } from "@/components/ui/Badge";
import { DisabledActionButton } from "@/components/ui/DisabledActionButton";
import { EmptyState } from "@/components/ui/EmptyState";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { useBrandContext } from "@/lib/brand-context";

const STUDIO_FORMATS = [
  { id: "script", label: "Structured Script Draft", summary: "Evidence-backed hook, technical breakdown & source attribution." },
  { id: "carousel", label: "Instagram Carousel Deck", summary: "Generated and rendered today from the CLI; finished decks land in Approvals for review." },
  { id: "video", label: "Narrated Vertical Reel / Short", summary: "TTS narration + FFmpeg composition. Not built yet." },
] as const;

export function StudioView() {
  const { activeBrand } = useBrandContext();
  const [activeTab, setActiveTab] = useState<(typeof STUDIO_FORMATS)[number]["id"]>("script");
  const currentTab = STUDIO_FORMATS.find((t) => t.id === activeTab) ?? STUDIO_FORMATS[0];

  return (
    <div className="space-y-6">
      <SurfaceCard
        icon={<Clapperboard />} title={`Content Studio Workspace — ${activeBrand.name}`}
        subtitle="Carousels are generated from the CLI today. In-dashboard generation is on the roadmap."
        badge={<Badge tone="info">CLI generation</Badge>}
      >
        <div className="mb-4 flex flex-wrap gap-2" role="tablist" aria-label="Studio output formats">
          {STUDIO_FORMATS.map((tab) => {
            const selected = tab.id === activeTab;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setActiveTab(tab.id)}
                className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                  selected
                    ? "border-sky-400/50 bg-sky-500/15 text-sky-200"
                    : "border-white/[0.1] bg-[var(--surface-inset)] text-slate-400 hover:text-slate-200"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="rounded-lg border border-white/[0.05] bg-[var(--surface-inset)] p-4 text-xs">
          <p className="font-semibold text-slate-200">{currentTab.label}</p>
          <p className="mt-1 text-slate-400">{currentTab.summary}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <DisabledActionButton label="Generate with Gemini" reason="Coming soon" />
            <DisabledActionButton label="Synthesize TTS Audio" reason="Coming soon" />
            <DisabledActionButton label="Render FFmpeg Video" reason="Coming soon" />
          </div>
        </div>
      </SurfaceCard>

      <EmptyState
        icon={<Clapperboard className="h-5 w-5" aria-hidden="true" />}
        title={`No production drafts saved for ${activeBrand.name}`}
        description={
          activeBrand.placeholder_unconfigured
            ? "App Promotion visual identity, product features, and messaging prompts remain unconfigured until owner brand setup."
            : "Drafts generated from the CLI appear in Approvals for review. Saving drafts here is coming soon."
        }
        phaseBadge="Coming soon"
        brandLabel={activeBrand.name}
      />
    </div>
  );
}

export function AssetsView() {
  const { activeBrand } = useBrandContext();
  return (
    <div className="space-y-6">
      <SurfaceCard
        icon={<FolderKanban />} title={`Brand Visual Assets — ${activeBrand.name}`}
        subtitle="Media storage remains strictly isolated per BrandProfile."
        badge={<DisabledActionButton label="Upload Brand Asset" reason="Coming soon" />}
      >
        <p className="text-xs leading-relaxed text-slate-400">
          {activeBrand.placeholder_unconfigured
            ? "App Promotion brand logo, screenshots, typography, and color tokens are intentionally left unconfigured until the owner supplies approved app branding."
            : "Rendered carousel slides are stored per brand and shown in Approvals and Analytics. A browsable library is coming soon."}
        </p>
      </SurfaceCard>

      <EmptyState
        icon={<FolderKanban className="h-5 w-5" aria-hidden="true" />}
        title={`Asset library for ${activeBrand.name}`}
        description="A browsable library of rendered slides, videos and audio is not built yet."
        phaseBadge="Coming soon"
        brandLabel={activeBrand.name}
      />
    </div>
  );
}

export { CalendarView } from "@/components/views/CalendarView";
export { AnalyticsView } from "@/components/views/AnalyticsView";

const WORKER_SPEC = [
  { name: "API control plane (media-os-api)", status: "Running", tone: "live" as const, note: "Health, auth, CSRF, brands, content review, publishing and analytics APIs on 127.0.0.1:8000." },
  { name: "Operator UI (media-os-web)", status: "Running", tone: "live" as const, note: "Next.js App Router BFF + UI on 127.0.0.1:3100." },
  { name: "Worker (media-os-worker)", status: "Running", tone: "live" as const, note: "Durable JobRun executor for control, research, publishing and analytics-sync queues. Holds AI and Postiz keys." },
  { name: "Scheduler & outbox (media-os-scheduler)", status: "Running", tone: "live" as const, note: "PostgreSQL-backed Asia/Kolkata scheduler, stale-lease sweeper and transactional outbox dispatcher." },
  { name: "Creative renderer (media-os-creative)", status: "Running", tone: "live" as const, note: "Headless Chromium on the creative queue renders carousel slides. No AI or publishing keys." },
  { name: "Research ingestion", status: "Built · sources off", tone: "disabled" as const, note: "Five verified fetchers; schedules disabled until the owner enables them. Reddit is blocked by policy." },
  { name: "Custom MCP server (services/mcp)", status: "Planned", tone: "disabled" as const, note: "Media OS MCP tool surface is not built yet." },
];

// Same order as WORKER_SPEC: API, UI, worker, scheduler, renderer, research, MCP.
const WORKER_ICONS = [Server, MonitorSmartphone, Cpu, Timer, Palette, Radar, Plug];

export function AgentsView() {
  return (
    <div className="space-y-6">
      <SurfaceCard
        icon={<Cpu />} title="Agent & worker processes"
        subtitle="Process roles under the 16 GB Windows + WSL2 Docker memory ceiling."
        badge={<Badge tone="live" dot>Durable jobs active</Badge>}
      >
        <div className="stagger-children space-y-2.5">
          {WORKER_SPEC.map((worker, i) => {
            const Icon = WORKER_ICONS[i] ?? Cpu;
            const live = worker.tone === "live";
            return (
              <div
                key={worker.name}
                className="surface-interactive tilt group flex flex-wrap items-center justify-between gap-3 rounded-lg border [--tilt-max:1.5] border-white/[0.05] bg-[var(--surface-inset)] px-4 py-3 text-xs"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <span
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-transform duration-300 group-hover:scale-110 ${
                      live
                        ? "border-sky-400/20 bg-gradient-to-b from-sky-400/15 to-sky-400/[0.03] text-sky-300 shadow-[0_0_18px_-6px_rgba(34,211,238,0.6)]"
                        : "border-white/[0.06] bg-white/[0.02] text-slate-500"
                    }`}
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium text-slate-200">
                      <StatusDot tone={worker.tone} />
                      {worker.name}
                    </p>
                    <p className="mt-0.5 text-slate-400">{worker.note}</p>
                  </div>
                </div>
                <Badge tone={worker.tone}>{worker.status}</Badge>
              </div>
            );
          })}
        </div>
      </SurfaceCard>
    </div>
  );
}
