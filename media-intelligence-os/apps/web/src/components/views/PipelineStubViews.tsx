"use client";

import React, { useState } from "react";
import {
  Bot,
  CalendarClock,
  Clapperboard,
  FolderKanban,
  LineChart,
} from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { DisabledActionButton } from "@/components/ui/DisabledActionButton";
import { EmptyState } from "@/components/ui/EmptyState";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { useBrandContext } from "@/lib/brand-context";

const STUDIO_FORMATS = [
  { id: "script", label: "Structured Script Draft", summary: "Evidence-backed hook, technical breakdown & source attribution." },
  { id: "carousel", label: "Instagram Carousel Deck", summary: "Slide outline & layout spec (rendering arrives Phase 7)." },
  { id: "video", label: "Narrated Vertical Reel / Short", summary: "Gemini TTS audio + FFmpeg composition (arrives Phase 7)." },
] as const;

export function StudioView() {
  const { activeBrand } = useBrandContext();
  const [activeTab, setActiveTab] = useState<(typeof STUDIO_FORMATS)[number]["id"]>("script");
  const currentTab = STUDIO_FORMATS.find((t) => t.id === activeTab) ?? STUDIO_FORMATS[0];

  return (
    <div className="space-y-6">
      <SurfaceCard
        title={`Content Studio Workspace — ${activeBrand.name}`}
        subtitle="Layout shell for Gemini Developer API structured authoring and FFmpeg assembly."
        badge={<Badge tone="disabled">Generation Disabled in Phase 4</Badge>}
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
                    : "border-slate-700/70 bg-slate-900/60 text-slate-400 hover:text-slate-200"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="rounded-lg border border-slate-800 bg-[#090b10] p-4 text-xs">
          <p className="font-semibold text-slate-200">{currentTab.label}</p>
          <p className="mt-1 text-slate-400">{currentTab.summary}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <DisabledActionButton label="Generate with Gemini" reason="Phase 7" />
            <DisabledActionButton label="Synthesize TTS Audio" reason="Phase 7" />
            <DisabledActionButton label="Render FFmpeg Video" reason="Phase 7" />
          </div>
        </div>
      </SurfaceCard>

      <EmptyState
        icon={<Clapperboard className="h-5 w-5" aria-hidden="true" />}
        title={`No production drafts saved for ${activeBrand.name}`}
        description={
          activeBrand.placeholder_unconfigured
            ? "App Promotion visual identity, product features, and messaging prompts remain unconfigured until owner brand setup."
            : "Creative generation, TTS synthesis, and carousel/video rendering are excluded from Phase 4 scope."
        }
        phaseBadge="Not Implemented — Phase 7"
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
        title={`Brand Visual Assets — ${activeBrand.name}`}
        subtitle="Media storage remains strictly isolated per BrandProfile."
        badge={<DisabledActionButton label="Upload Brand Asset" reason="Phase 7" />}
      >
        <p className="text-xs leading-relaxed text-slate-400">
          {activeBrand.placeholder_unconfigured
            ? "App Promotion brand logo, screenshots, typography, and color tokens are intentionally left unconfigured until the owner supplies approved app branding."
            : "Rendered carousels, Gemini TTS narration tracks, and thumbnails will populate here when Phase 7 media pipelines run."}
        </p>
      </SurfaceCard>

      <EmptyState
        icon={<FolderKanban className="h-5 w-5" aria-hidden="true" />}
        title={`Asset library empty for ${activeBrand.name}`}
        description="Zero generated slides, videos, or audio files exist in Phase 4."
        phaseBadge="Not Implemented — Phase 7"
        brandLabel={activeBrand.name}
      />
    </div>
  );
}

export function CalendarView() {
  const { activeBrand } = useBrandContext();
  return (
    <div className="space-y-6">
      <SurfaceCard
        title={`Publishing Cadence — ${activeBrand.name}`}
        subtitle="Multi-brand schedule separation across Instagram Professional and YouTube targets."
        badge={<DisabledActionButton label="Queue in Postiz" reason="Phase 8" />}
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {["Instagram Professional Slot", "YouTube Channel Slot"].map((slot) => (
            <div
              key={slot}
              className="rounded-lg border border-slate-800 bg-[#090b10] p-4 text-xs"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold text-slate-200">{slot}</span>
                <Badge tone="disabled">Not Connected</Badge>
              </div>
              <p className="mt-1.5 text-slate-400">
                Brand Scope: <span className="text-slate-200">{activeBrand.name}</span>
              </p>
              <p className="mt-1 text-slate-500">Cadence unlocked in Phase 8 after Postiz channel binding.</p>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <EmptyState
        icon={<CalendarClock className="h-5 w-5" aria-hidden="true" />}
        title={`No scheduled items for ${activeBrand.name}`}
        description="Automated scheduling and Postiz write operations are disabled in Phase 4."
        phaseBadge="Not Implemented — Phase 8"
        brandLabel={activeBrand.name}
      />
    </div>
  );
}

export function AnalyticsView() {
  const { activeBrand } = useBrandContext();
  return (
    <div className="space-y-6">
      <SurfaceCard
        title={`Performance Telemetry — ${activeBrand.name}`}
        subtitle="Postiz analytics ingestion and content learning signals remain disabled in Phase 4."
        badge={<Badge tone="disabled">No Fake Charts</Badge>}
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {["Reach", "Engagement Rate", "Saves / Shares", "Short Completion"].map((metric) => (
            <div key={metric} className="rounded-lg border border-slate-800 bg-[#090b10] p-4">
              <span className="text-xs text-slate-400">{metric}</span>
              <p className="mt-2 font-mono text-2xl font-semibold text-slate-500">--</p>
              <p className="mt-1 text-[11px] text-slate-500">Phase 9 target</p>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <EmptyState
        icon={<LineChart className="h-5 w-5" aria-hidden="true" />}
        title={`No performance records collected for ${activeBrand.name}`}
        description="Read-only Postiz analytics ingestion and feedback learning loops are deferred to Phase 9."
        phaseBadge="Not Implemented — Phase 9"
        brandLabel={activeBrand.name}
      />
    </div>
  );
}

const WORKER_SPEC = [
  { name: "FastAPI Control Plane (media-os-api)", status: "Running (Phase 4)", tone: "live" as const, note: "Serves health, auth, CSRF, PostgreSQL brands & workflow APIs on 127.0.0.1:8000." },
  { name: "Next.js Operator UI (media-os-web)", status: "Running (Phase 4)", tone: "live" as const, note: "Standalone App Router BFF + UI on 127.0.0.1:3000." },
  { name: "Celery Worker (media-os-worker)", status: "Running (Phase 4)", tone: "live" as const, note: "JSON-serialized durable JobRun executor (concurrency=1) for internal control/maintenance tasks." },
  { name: "Persistent Scheduler & Outbox (media-os-scheduler)", status: "Running (Phase 4)", tone: "live" as const, note: "PostgreSQL-backed Asia/Kolkata scheduler, stale-lease sweeper & transactional outbox dispatcher." },
  { name: "Research Worker (workers/research)", status: "Deferred — Phase 5", tone: "disabled" as const, note: "Source connectors & normalization not yet started." },
  { name: "Creative & FFmpeg Worker (workers/creative)", status: "Deferred — Phase 7", tone: "disabled" as const, note: "Target Celery concurrency=1 under 16 GB host memory budget." },
  { name: "Custom MCP Server (services/mcp)", status: "Deferred — Phase 10", tone: "disabled" as const, note: "Custom Media OS MCP tool surface arrives in Phase 10." },
];

export function AgentsView() {
  return (
    <div className="space-y-6">
      <SurfaceCard
        title="Agent & Worker Process Topology"
        subtitle="Honest process status under the 16 GB Windows + WSL2 Docker memory ceiling."
        badge={<Badge tone="live">Phase 4 Durable Jobs Active</Badge>}
      >
        <div className="space-y-2.5">
          {WORKER_SPEC.map((worker) => (
            <div
              key={worker.name}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-[#090b10] px-4 py-3 text-xs"
            >
              <div>
                <p className="font-semibold text-slate-200">{worker.name}</p>
                <p className="mt-0.5 text-slate-400">{worker.note}</p>
              </div>
              <Badge tone={worker.tone}>{worker.status}</Badge>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <EmptyState
        icon={<Bot className="h-5 w-5" aria-hidden="true" />}
        title="Domain Research & Creative Workers Deferred"
        description="Core durable job execution, transactional outbox, and persistent scheduling are live in Phase 4; research ingestion (Phase 5) and creative/FFmpeg pipelines (Phase 7) remain unimplemented."
        phaseBadge="Phase 4 Infrastructure Live • Domain Workers Phase 5-10"
      />
    </div>
  );
}
