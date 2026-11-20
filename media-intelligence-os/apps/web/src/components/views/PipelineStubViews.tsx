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

import { Badge, StatusDot, type BadgeTone } from "@/components/ui/Badge";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import type { ProcessStatusItem, SystemStatusResponse } from "@/lib/types";

export { StudioView } from "@/components/views/StudioView";
export { AssetsView } from "@/components/views/AssetsView";
export { CalendarView } from "@/components/views/CalendarView";
export { AnalyticsView } from "@/components/views/AnalyticsView";

type Tone = BadgeTone;

interface ProcessRow {
  name: string;
  status: string;
  tone: Tone;
  note: string;
  Icon: typeof Cpu;
}

const STATE_LABEL: Record<ProcessStatusItem["state"], { status: string; tone: Tone }> = {
  running: { status: "Running", tone: "live" },
  idle: { status: "Idle · ready", tone: "info" },
  stalled: { status: "Stuck", tone: "blocked" },
  down: { status: "Not running", tone: "blocked" },
  off: { status: "Switched off", tone: "disabled" },
  unknown: { status: "Unknown", tone: "neutral" },
};

const LEDGER_PROCESSES: ReadonlyArray<{ key: string; name: string; Icon: typeof Cpu; role: string }> = [
  { key: "worker", name: "Worker (media-os-worker)", Icon: Cpu, role: "Runs durable jobs: AI writing, publishing, analytics sync, alerts." },
  { key: "scheduler", name: "Scheduler & outbox (media-os-scheduler)", Icon: Timer, role: "Queues scheduled jobs and dispatches the outbox." },
  { key: "creative", name: "Creative renderer (media-os-creative)", Icon: Palette, role: "Headless Chromium that renders carousel slides on demand." },
  { key: "research", name: "Research ingestion", Icon: Radar, role: "Official-source fetchers. Reddit is blocked by policy." },
];

function buildRows(
  processes: ProcessStatusItem[] | null,
  systemStatus: SystemStatusResponse | null,
): ProcessRow[] {
  const apiUp = systemStatus !== null;
  const rows: ProcessRow[] = [
    {
      name: "API control plane (media-os-api)",
      Icon: Server,
      ...(apiUp ? { status: "Running", tone: "live" as const } : { status: "Not answering", tone: "blocked" as const }),
      note: apiUp
        ? `Database ${systemStatus.database_status} · Redis ${systemStatus.redis_status}.`
        : "The dashboard couldn't reach the API.",
    },
    {
      name: "Operator UI (media-os-web)",
      Icon: MonitorSmartphone,
      status: "Running",
      tone: "live",
      note: "You're looking at it.",
    },
  ];
  for (const proc of LEDGER_PROCESSES) {
    const item = processes?.find((p) => p.key === proc.key);
    const label = STATE_LABEL[item?.state ?? "unknown"];
    rows.push({
      name: proc.name,
      Icon: proc.Icon,
      status: label.status,
      tone: label.tone,
      note: item ? `${proc.role} ${item.detail}` : `${proc.role} Status unavailable.`,
    });
  }
  const postiz = systemStatus?.postiz;
  rows.push({
    name: "Postiz (separate stack)",
    Icon: Plug,
    ...(postiz?.status === "reachable"
      ? { status: "Answering", tone: "live" as const }
      : postiz
        ? { status: "Not answering", tone: "blocked" as const }
        : { status: "Unknown", tone: "neutral" as const }),
    note: "Publishes scheduled posts to Instagram and Facebook.",
  });
  return rows;
}

export function AgentsView({
  processes = null,
  systemStatus = null,
}: {
  processes?: ProcessStatusItem[] | null;
  systemStatus?: SystemStatusResponse | null;
}) {
  const rows = buildRows(processes, systemStatus);
  const problems = rows.filter((r) => r.tone === "blocked").length;
  return (
    <div className="space-y-6">
      <SurfaceCard
        icon={<Cpu />} title="Agent & worker processes"
        subtitle="Live state, worked out from the job ledger when this page loaded."
        badge={
          problems ? (
            <Badge tone="blocked" dot>{problems} need attention</Badge>
          ) : (
            <Badge tone="live" dot>All good</Badge>
          )
        }
      >
        <div className="stagger-children space-y-2.5">
          {rows.map((worker) => {
            const Icon = worker.Icon;
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
