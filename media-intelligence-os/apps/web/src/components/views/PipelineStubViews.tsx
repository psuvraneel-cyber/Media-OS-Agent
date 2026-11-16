"use client";

import React from "react";
import {
  Cpu,
  MonitorSmartphone,
  Palette,
  Plug,
  Radar,
  Server,
  Timer,
} from "lucide-react";

import { Badge, StatusDot } from "@/components/ui/Badge";
import { SurfaceCard } from "@/components/ui/SurfaceCard";

export { StudioView } from "@/components/views/StudioView";
export { AssetsView } from "@/components/views/AssetsView";
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
