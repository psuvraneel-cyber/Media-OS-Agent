"use client";

import React, { useState } from "react";
import {
  Clapperboard,
  FolderKanban,
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

        <div className="rounded-lg border border-white/[0.05] bg-[var(--surface-inset)] p-4 text-xs">
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

export function AgentsView() {
  return (
    <div className="space-y-6">
      <SurfaceCard
        title="Agent & worker processes"
        subtitle="Process roles under the 16 GB Windows + WSL2 Docker memory ceiling."
        badge={<Badge tone="live" dot>Durable jobs active</Badge>}
      >
        <div className="space-y-2.5">
          {WORKER_SPEC.map((worker) => (
            <div
              key={worker.name}
              className="surface-interactive flex flex-wrap items-center justify-between gap-3 rounded-lg border border-white/[0.05] bg-[var(--surface-inset)] px-4 py-3 text-xs"
            >
              <div className="min-w-0">
                <p className="font-medium text-slate-200">{worker.name}</p>
                <p className="mt-0.5 text-slate-400">{worker.note}</p>
              </div>
              <Badge tone={worker.tone}>{worker.status}</Badge>
            </div>
          ))}
        </div>
      </SurfaceCard>
    </div>
  );
}
