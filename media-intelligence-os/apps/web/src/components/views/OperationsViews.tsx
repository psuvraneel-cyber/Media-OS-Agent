"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Plug,
  RefreshCw,
  Share2,
  Shield,
  SlidersHorizontal,
  Terminal,
} from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { DisabledActionButton } from "@/components/ui/DisabledActionButton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";
import type {
  AuditLogResponse,
  BrandProfileSummary,
  ChannelAccountSummary,
  SessionUser,
  SystemStatusResponse,
  TelemetryLogResponse,
} from "@/lib/types";

export function IntegrationsView({
  systemStatus,
  channels,
  errorMessage,
}: {
  systemStatus: SystemStatusResponse | null;
  channels: ChannelAccountSummary[];
  errorMessage?: string;
}) {
  const { activeBrand } = useBrandContext();
  const brandChannels = channels.filter((c) => c.brand_id === activeBrand.brand_id);

  return (
    <div className="space-y-6">
      {errorMessage && <ErrorBanner message={errorMessage} />}

      <SurfaceCard
        icon={<Plug />} title="External & Infrastructure Boundaries"
        subtitle="Postiz remains completely separate in postiz-runtime; application PostgreSQL and Redis run on isolated media-os-net."
        badge={
          <Badge tone={systemStatus?.postiz.status === "reachable" ? "live" : "blocked"}>
            Postiz: {systemStatus?.postiz.status ?? "Unknown"}
          </Badge>
        }
      >
        <div className="stagger-children grid grid-cols-1 gap-3 md:grid-cols-3 text-xs">
          <div className="surface-interactive tilt rounded-lg border border-white/[0.08] bg-[var(--surface-inset)] p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-slate-200">Application PostgreSQL &amp; Redis</span>
              <Badge tone={systemStatus?.persistent_storage ? "live" : "blocked"}>
                {systemStatus?.persistent_storage ? "Ready" : "Unavailable"}
              </Badge>
            </div>
            <p className="mt-1.5 text-slate-400">
              Schema {systemStatus?.schema_version ?? "unknown"} • Redis{" "}
              {systemStatus?.redis_status ?? "unknown"}
            </p>
          </div>

          <div className="surface-interactive tilt rounded-lg border border-white/[0.08] bg-[var(--surface-inset)] p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-slate-200">Postiz Standalone (v2.25.3)</span>
              <Badge tone={systemStatus?.postiz.status === "reachable" ? "live" : "blocked"}>
                {systemStatus?.postiz.status ?? "Unknown"}
              </Badge>
            </div>
            <p className="mt-1.5 text-slate-400">
              {systemStatus?.postiz.detail ?? "Probed read-only without exposing API secrets."}
            </p>
            <p className="mt-1 text-[11px] text-slate-500">
              Posts publish through the Postiz public API from the worker; channel OAuth stays inside Postiz.
            </p>
          </div>

          <div className="surface-interactive tilt rounded-lg border border-white/[0.08] bg-[var(--surface-inset)] p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-slate-200">AI providers</span>
              <Badge tone="info">Server-side only</Badge>
            </div>
            <p className="mt-1.5 text-slate-400">
              AI generation runs in the worker through a multi-provider router. Provider keys never reach the browser.
            </p>
          </div>
        </div>
      </SurfaceCard>

      <SurfaceCard
        icon={<Share2 />} title={`Channel Accounts — ${activeBrand.name}`}
        subtitle="Persisted PostgreSQL ChannelAccount rows filtered strictly to the active brand."
        badge={<DisabledActionButton label="Bind Postiz Channel" reason="Phase 8" />}
      >
        {brandChannels.length > 0 ? (
          <div className="stagger-children grid grid-cols-1 gap-3 md:grid-cols-2">
            {brandChannels.map((ch) => (
              <div
                key={ch.channel_id}
                className="rounded-lg border border-white/[0.06] bg-[var(--surface-inset)] p-4 text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-slate-100">{ch.display_label}</span>
                  <Badge tone="disabled">{ch.connection_status}</Badge>
                </div>
                <p className="mt-1.5 font-mono text-[11px] text-slate-400">
                  platform={ch.platform} | publishing={String(ch.publishing_enabled)}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<Plug className="h-5 w-5" aria-hidden="true" />}
            title={`No connected social channels for ${activeBrand.name}`}
            description={
              activeBrand.placeholder_unconfigured
                ? "App Promotion brand details and social channels remain unconfigured. Zero accounts are fabricated."
                : "No Instagram or YouTube ChannelAccount records are linked yet. Postiz OAuth channel binding arrives in Phase 8."
            }
            phaseBadge="Empty State — Phase 8 OAuth"
            brandLabel={activeBrand.name}
          />
        )}
      </SurfaceCard>
    </div>
  );
}

export function SettingsView({
  sessionUser,
  systemStatus,
}: {
  sessionUser: SessionUser;
  systemStatus: SystemStatusResponse | null;
}) {
  const router = useRouter();
  const { activeBrand } = useBrandContext();
  const [toggling, setToggling] = useState(false);
  const [toggleError, setToggleError] = useState<string | null>(null);
  const [activationOverride, setActivationOverride] = useState<{
    brandId: string;
    active: boolean;
  } | null>(null);

  const brandActiveState =
    activationOverride?.brandId === activeBrand.brand_id
      ? activationOverride.active
      : activeBrand.active;

  async function handleToggleActivation() {
    setToggling(true);
    setToggleError(null);
    try {
      const updated = await browserApiFetch<BrandProfileSummary>(
        `/brands/${encodeURIComponent(activeBrand.brand_id)}/activation`,
        {
          method: "POST",
          csrfToken: sessionUser.csrf_token,
          body: JSON.stringify({ is_active: !brandActiveState }),
        },
      );
      setActivationOverride({ brandId: activeBrand.brand_id, active: updated.active });
      router.refresh();
    } catch (err) {
      if (err instanceof ApiClientError) {
        setToggleError(`${err.code}: ${err.message}`);
      } else {
        setToggleError("Failed to update brand activation status.");
      }
    } finally {
      setToggling(false);
    }
  }

  return (
    <div className="space-y-6">
      {toggleError && <ErrorBanner message={toggleError} />}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SurfaceCard
          icon={<SlidersHorizontal />} title={`BrandProfile Spec — ${activeBrand.name}`}
          subtitle="Persisted PostgreSQL brand configuration matching PRD & ADR-014."
          badge={
            <Badge tone={activeBrand.data_origin === "postgresql" ? "live" : "prototype"}>
              {activeBrand.data_origin === "postgresql" ? "PostgreSQL" : "Fixture"}
            </Badge>
          }
        >
          <dl className="space-y-3 text-xs">
            <div>
              <dt className="text-slate-400">Brand Slug &amp; UUID</dt>
              <dd className="mt-0.5 font-mono text-slate-200">
                {activeBrand.slug} ({activeBrand.brand_id})
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">Niche Focus</dt>
              <dd className="mt-0.5 text-slate-200">{activeBrand.niche}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Target Audience</dt>
              <dd className="mt-0.5 text-slate-200">{activeBrand.target_audience}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Tone of Voice</dt>
              <dd className="mt-0.5 text-slate-200">{activeBrand.tone_of_voice}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Approval Policy, Language &amp; Timezone</dt>
              <dd className="mt-0.5 font-mono text-slate-200">
                {activeBrand.approval_policy} | lang={activeBrand.language} | tz=
                {activeBrand.timezone}
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">Configuration &amp; Activation State</dt>
              <dd className="mt-1 flex flex-wrap items-center gap-2">
                {activeBrand.placeholder_unconfigured ? (
                  <Badge tone="prototype">
                    Unconfigured App Identity — Owner Setup Required
                  </Badge>
                ) : (
                  <Badge tone="live">Configured Technical Profile</Badge>
                )}
                <Badge tone={brandActiveState ? "live" : "disabled"}>
                  {brandActiveState ? "Active" : "Inactive"}
                </Badge>
                {activeBrand.data_origin === "postgresql" && (
                  <button
                    type="button"
                    onClick={handleToggleActivation}
                    disabled={toggling}
                    className="rounded-lg btn-press border border-white/[0.1] bg-white/[0.04] px-2.5 py-1 text-[11px] font-medium text-slate-200 hover:border-white/[0.18] hover:bg-white/[0.08] disabled:opacity-50"
                  >
                    {toggling
                      ? "Updating..."
                      : brandActiveState
                        ? "Deactivate Brand"
                        : "Activate Brand"}
                  </button>
                )}
              </dd>
            </div>
          </dl>
        </SurfaceCard>

        <SurfaceCard
          icon={<Shield />} title="Owner Session & Security Posture"
          subtitle="PostgreSQL session persistence, Redis rate limiting, and CSRF guarantees."
          badge={
            <Badge tone="live">
              <Shield className="h-3 w-3" aria-hidden="true" />
              CSRF Active
            </Badge>
          }
        >
          <dl className="space-y-3 text-xs">
            <div>
              <dt className="text-slate-400">Authenticated Principal</dt>
              <dd className="mt-0.5 font-mono text-slate-200">
                {sessionUser.username} (role={sessionUser.role})
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">Session Expiration (UTC)</dt>
              <dd className="mt-0.5 font-mono text-slate-200">{sessionUser.expires_at_utc}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Authentication Strategy</dt>
              <dd className="mt-0.5 text-slate-200">
                {systemStatus?.auth_mode ?? "single_owner_postgresql_cookie"} (Argon2id +
                HMAC-SHA256)
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">Persistent Storage &amp; Display Timezone</dt>
              <dd className="mt-0.5 text-slate-300">
                {systemStatus?.persistent_storage
                  ? `PostgreSQL active (schema=${systemStatus.schema_version ?? "head"}), Redis=${systemStatus.redis_status}, tz=${systemStatus.display_timezone}`
                  : "Persistent storage not active."}
              </dd>
            </div>
          </dl>
        </SurfaceCard>
      </div>
    </div>
  );
}

/** Colour-codes audit actions by domain so the trail scans at a glance. */
function actionTone(action: string, outcome: string): string {
  if (outcome !== "SUCCESS" || action.includes("FAILED") || action.includes("REJECT")) {
    return "border-rose-400/25 bg-rose-400/10 text-rose-300";
  }
  if (action.startsWith("AUTH_") || action.startsWith("OWNER_")) {
    return "border-indigo-400/25 bg-indigo-400/10 text-indigo-300";
  }
  if (action.includes("SCHEDULE") || action.includes("PUBLISH")) {
    return "border-emerald-400/20 bg-emerald-400/10 text-emerald-300";
  }
  if (action.startsWith("CAROUSEL_") || action.startsWith("CONTENT_")) {
    return "border-sky-400/25 bg-sky-400/10 text-sky-300";
  }
  if (action.startsWith("BRAND_")) {
    return "border-amber-400/20 bg-amber-400/10 text-amber-200";
  }
  return "border-white/[0.08] bg-white/[0.03] text-slate-300";
}

export function LogsView({
  initialLogs,
  initialAuditEvents = null,
}: {
  initialLogs: TelemetryLogResponse | null;
  initialAuditEvents?: AuditLogResponse | null;
}) {
  const [logs, setLogs] = useState<TelemetryLogResponse | null>(initialLogs);
  const [auditEvents, setAuditEvents] = useState<AuditLogResponse | null>(initialAuditEvents);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      const [freshLogs, freshAudit] = await Promise.all([
        browserApiFetch<TelemetryLogResponse>("/system/logs?limit=40"),
        browserApiFetch<AuditLogResponse>("/system/audit-events?limit=40"),
      ]);
      setLogs(freshLogs);
      setAuditEvents(freshAudit);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(`${err.code}: ${err.message}`);
      } else {
        setError("Failed to refresh runtime logs.");
      }
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <SurfaceCard
        icon={<Shield />} title="Persisted Security & Brand Audit Trail (PostgreSQL)"
        subtitle="Durable 365-day audit log of owner authentication, brand updates, and workflow state transitions."
        badge={
          <div className="flex items-center gap-2">
            <Badge tone="live">PostgreSQL Persisted</Badge>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing}
              className="inline-flex items-center gap-1.5 rounded-lg btn-press border border-white/[0.1] bg-white/[0.04] px-2.5 py-1 text-xs font-medium text-slate-200 hover:border-white/[0.18] hover:bg-white/[0.08] disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              <span>Refresh Logs</span>
            </button>
          </div>
        }
      >
        {auditEvents && auditEvents.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-white/[0.06] text-[11px] text-slate-400 uppercase">
                  <th className="py-2 pr-3 font-medium">Timestamp</th>
                  <th className="py-2 pr-3 font-medium">Action</th>
                  <th className="py-2 pr-3 font-medium">Actor</th>
                  <th className="py-2 pr-3 font-medium">Target</th>
                  <th className="py-2 font-medium">Outcome</th>
                </tr>
              </thead>
              <tbody className="rows-in divide-y divide-white/[0.05] font-mono text-[11px]">
                {auditEvents.items.map((evt) => (
                  <tr key={evt.id}>
                    <td className="py-2 pr-3 pl-2 whitespace-nowrap text-slate-500">
                      {evt.timestamp_utc}
                    </td>
                    <td className="py-2 pr-3">
                      <span
                        className={`inline-flex rounded-md border px-1.5 py-0.5 font-semibold ${actionTone(evt.action, evt.outcome)}`}
                      >
                        {evt.action}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-slate-200">{evt.actor_id}</td>
                    <td className="py-2 pr-3 text-slate-400">
                      {evt.target_type}:{evt.target_id.slice(0, 24)}
                    </td>
                    <td className="py-2">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wide ${
                          evt.outcome === "SUCCESS"
                            ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
                            : "border-amber-400/25 bg-amber-400/10 text-amber-300"
                        }`}
                      >
                        <span
                          aria-hidden="true"
                          className={`h-1.5 w-1.5 rounded-full ${
                            evt.outcome === "SUCCESS"
                              ? "bg-emerald-400 shadow-[0_0_6px_rgba(45,212,191,0.9)]"
                              : "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.9)]"
                          }`}
                        />
                        {evt.outcome}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="flex items-center gap-2 py-4 text-xs text-slate-400">
            <Shield className="h-4 w-4" aria-hidden="true" />
            <span>No persisted audit events available yet.</span>
          </div>
        )}
      </SurfaceCard>

      <SurfaceCard
        icon={<Terminal />} title="Sanitized HTTP Correlation Trace"
        subtitle={
          logs?.note ??
          "Ephemeral in-memory telemetry for current container uptime (secrets redacted)."
        }
        badge={
          <div className="flex items-center gap-2">
            <Badge tone="prototype">Ephemeral Ring Buffer</Badge>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing}
              className="inline-flex items-center gap-1.5 rounded-lg btn-press border border-white/[0.1] bg-white/[0.04] px-2.5 py-1 text-xs font-medium text-slate-200 hover:border-white/[0.18] hover:bg-white/[0.08] disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              <span>Refresh</span>
            </button>
          </div>
        }
      >
        {logs && logs.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-white/[0.06] text-[11px] text-slate-400 uppercase">
                  <th className="py-2 pr-3 font-medium">Timestamp</th>
                  <th className="py-2 pr-3 font-medium">Request ID</th>
                  <th className="py-2 pr-3 font-medium">Method &amp; Route</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 font-medium">Duration</th>
                </tr>
              </thead>
              <tbody className="rows-in divide-y divide-white/[0.05] font-mono text-[11px]">
                {logs.items.map((item, idx) => (
                  <tr key={`${item.request_id}-${idx}`} className="hover:bg-white/[0.02]">
                    <td className="py-2 pr-3 whitespace-nowrap text-slate-400">
                      {item.timestamp_utc}
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap text-sky-300">
                      {item.request_id.slice(0, 16)}
                    </td>
                    <td className="py-2 pr-3 text-slate-200">
                      {item.method} {item.route}
                    </td>
                    <td className="py-2 pr-3">
                      <span
                        className={
                          item.status_code >= 500
                            ? "text-rose-400"
                            : item.status_code >= 400
                              ? "text-amber-300"
                              : "text-emerald-300"
                        }
                      >
                        {item.status_code}
                      </span>
                    </td>
                    <td className="py-2 whitespace-nowrap text-slate-400">{item.duration_ms} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="flex items-center gap-2 py-6 text-xs text-slate-400">
            <Terminal className="h-4 w-4" aria-hidden="true" />
            <span>No request telemetry recorded yet during current uptime.</span>
          </div>
        )}
      </SurfaceCard>
    </div>
  );
}
