"use client";

import React from "react";
import Link from "next/link";
import { Activity, ArrowRight, Database, Lock, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { ErrorBanner } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { useBrandContext } from "@/lib/brand-context";
import type { SystemStatusResponse } from "@/lib/types";

interface DashboardOverviewViewProps {
  systemStatus: SystemStatusResponse | null;
  errorMessage?: string;
}

const KPI_PLACEHOLDERS = [
  { label: "Approved Topics", phase: "Phase 5-6", detail: "Discovery & ranking disabled" },
  { label: "Production Drafts", phase: "Phase 7", detail: "Gemini & FFmpeg studio disabled" },
  { label: "Pending Approvals", phase: "Phase 6-8", detail: "No pending review artifacts" },
  { label: "Scheduled Posts", phase: "Phase 8", detail: "Postiz write bridge disabled" },
];

export function DashboardOverviewView({
  systemStatus,
  errorMessage,
}: DashboardOverviewViewProps) {
  const { activeBrand, buildHrefWithBrand } = useBrandContext();

  const storageSummary = systemStatus
    ? systemStatus.persistent_storage
      ? "PostgreSQL + Redis"
      : systemStatus.database_status === "unreachable"
        ? "DB Unreachable"
        : "Not Configured"
    : "Unknown";

  return (
    <div className="space-y-6">
      {errorMessage && (
        <ErrorBanner title="Control-Plane Telemetry Warning" message={errorMessage} />
      )}

      {/* Top status summary */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SurfaceCard>
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">API Control Plane</span>
            <Activity className="h-4 w-4 text-emerald-400" aria-hidden="true" />
          </div>
          <p className="mt-2 text-lg font-semibold text-slate-100">
            {systemStatus ? "Online" : "Degraded"}
          </p>
          <p className="mt-1 text-xs text-slate-400">
            {systemStatus
              ? `${systemStatus.service} (${systemStatus.phase})`
              : "Status unavailable"}
          </p>
        </SurfaceCard>

        <SurfaceCard>
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">Session Security</span>
            <ShieldCheck className="h-4 w-4 text-sky-400" aria-hidden="true" />
          </div>
          <p className="mt-2 text-lg font-semibold text-slate-100">Cookie + CSRF</p>
          <p className="mt-1 text-xs text-slate-400">
            PostgreSQL session revocation &amp; Redis rate limiting active
          </p>
        </SurfaceCard>

        <SurfaceCard>
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">Application Storage</span>
            <Database
              className={`h-4 w-4 ${systemStatus?.persistent_storage ? "text-emerald-400" : "text-slate-400"}`}
              aria-hidden="true"
            />
          </div>
          <p className="mt-2 text-lg font-semibold text-slate-100">{storageSummary}</p>
          <p className="mt-1 text-xs text-slate-400">
            {systemStatus?.persistent_storage
              ? `Schema ${systemStatus.schema_version ?? "head"} • Redis ${systemStatus.redis_status}`
              : "PostgreSQL & Redis health unavailable"}
          </p>
        </SurfaceCard>

        <SurfaceCard>
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">Postiz Gateway (v2.25.3)</span>
            <Lock className="h-4 w-4 text-amber-400" aria-hidden="true" />
          </div>
          <div className="mt-2 flex items-center gap-2">
            <p className="text-lg font-semibold capitalize text-slate-100">
              {systemStatus?.postiz.status ?? "Unknown"}
            </p>
            <Badge
              tone={
                systemStatus?.postiz.status === "reachable"
                  ? "live"
                  : systemStatus?.postiz.status === "unreachable"
                    ? "blocked"
                    : "disabled"
              }
            >
              Read-Only Probe
            </Badge>
          </div>
          <p className="mt-1 text-xs text-slate-400">
            {systemStatus?.postiz.detail ?? "Publishing bridge deferred to Phase 8"}
          </p>
        </SurfaceCard>
      </div>

      {/* Phase-gated empty KPI slots */}
      <SurfaceCard
        title={`Pipeline Metrics — ${activeBrand.name}`}
        subtitle="Metrics remain intentionally empty until their corresponding ingestion and production phases are built."
        badge={<Badge tone="disabled">Zero Simulated Metrics</Badge>}
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {KPI_PLACEHOLDERS.map((kpi) => (
            <div
              key={kpi.label}
              className="rounded-lg border border-slate-800 bg-[#090b10] p-4"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-slate-400">{kpi.label}</span>
                <Badge tone="disabled">{kpi.phase}</Badge>
              </div>
              <p className="mt-2 font-mono text-2xl font-semibold text-slate-500">--</p>
              <p className="mt-1 text-[11px] text-slate-500">{kpi.detail}</p>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* Active brand specification */}
        <SurfaceCard
          title="Active Brand Context"
          subtitle="Persisted multi-brand isolation backed by PostgreSQL BrandProfile."
          badge={
            activeBrand.placeholder_unconfigured ? (
              <Badge tone="prototype">Unconfigured Placeholder</Badge>
            ) : activeBrand.data_origin === "postgresql" ? (
              <Badge tone="live">PostgreSQL</Badge>
            ) : (
              <Badge tone="prototype">Prototype Fixture</Badge>
            )
          }
          className="lg:col-span-2"
        >
          <div className="space-y-3 text-xs">
            <div>
              <p className="text-slate-400">Brand Slug &amp; ID</p>
              <p className="mt-0.5 font-mono text-slate-200">
                {activeBrand.slug} ({activeBrand.brand_id})
              </p>
            </div>
            <div>
              <p className="text-slate-400">Editorial Description</p>
              <p className="mt-0.5 leading-relaxed text-slate-300">{activeBrand.description}</p>
            </div>
            {activeBrand.target_audience && (
              <div>
                <p className="text-slate-400">Target Audience &amp; Timezone</p>
                <p className="mt-0.5 text-slate-300">
                  {activeBrand.target_audience} •{" "}
                  <span className="font-mono text-slate-200">{activeBrand.timezone}</span>
                </p>
              </div>
            )}
            <div>
              <p className="text-slate-400">Content Pillars</p>
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {activeBrand.content_pillars.map((pillar) => (
                  <li
                    key={pillar}
                    className="rounded border border-slate-700/70 bg-slate-800/60 px-2 py-0.5 text-[11px] text-slate-200"
                  >
                    {pillar}
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-wrap gap-2 pt-2">
              <Link
                href={buildHrefWithBrand("/integrations")}
                className="inline-flex items-center gap-1.5 rounded-lg border border-sky-400/35 bg-sky-500/12 px-3 py-1.5 font-medium text-sky-200 hover:bg-sky-500/20"
              >
                <span>Inspect Channels</span>
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
              <Link
                href={buildHrefWithBrand("/settings")}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800/70 px-3 py-1.5 font-medium text-slate-200 hover:bg-slate-800"
              >
                <span>Brand Settings</span>
              </Link>
            </div>
          </div>
        </SurfaceCard>

        {/* System module boundary table */}
        <SurfaceCard
          title="Architectural Boundary Matrix"
          subtitle="Verified live posture across Phase 4 control plane and deferred subsystems."
          className="lg:col-span-3"
        >
          {systemStatus ? (
            <div className="space-y-2">
              {systemStatus.modules.map((mod) => {
                const isLive = mod.status === "active_phase_3" || mod.status === "active_phase_4";
                return (
                  <div
                    key={mod.module_id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-[#090b10] px-3.5 py-2.5 text-xs"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold text-slate-200">{mod.title}</p>
                      <p className="mt-0.5 text-[11px] text-slate-400">{mod.summary}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge
                        tone={
                          isLive
                            ? "live"
                            : mod.status === "blocked_by_policy"
                              ? "blocked"
                              : "disabled"
                        }
                      >
                        {isLive
                          ? "Active"
                          : mod.status === "blocked_by_policy"
                            ? "Blocked"
                            : mod.target_phase}
                      </Badge>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-slate-400">
              Module status could not be loaded from /api/v1/system/status.
            </p>
          )}
        </SurfaceCard>
      </div>
    </div>
  );
}
