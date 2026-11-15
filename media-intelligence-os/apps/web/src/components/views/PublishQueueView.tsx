"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  CalendarClock,
  ExternalLink,
  ImageOff,
  RefreshCw,
  Send,
} from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner, LoadingSkeleton } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";
import type { CarouselListResponse, CarouselSummary } from "@/lib/types";

const PLATFORMS = ["INSTAGRAM", "FACEBOOK"] as const;
type Platform = (typeof PLATFORMS)[number];

const LANGUAGE_LABEL: Record<string, string> = { BN: "Bengali", EN: "English", BN_EN: "Bilingual" };

function statusTone(status: string): BadgeTone {
  if (status === "PUBLISHED") return "live";
  if (status === "SCHEDULED") return "info";
  if (status === "OVERDUE") return "prototype";
  if (status === "FAILED" || status === "CANCELLED") return "blocked";
  return "prototype";
}

/** Next evening slot (19:00 local) at least ~1 hour away, as a datetime-local string. */
export function defaultSlot(now: Date = new Date()): string {
  const d = new Date(now);
  d.setHours(19, 0, 0, 0);
  if (d.getTime() - now.getTime() < 60 * 60 * 1000) d.setDate(d.getDate() + 1);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function errorText(err: unknown): string {
  return err instanceof ApiClientError ? `${err.message} (${err.code})` : "Request failed.";
}

function fmt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function ScheduleForm({
  item,
  brand,
  onDone,
}: {
  item: CarouselSummary;
  brand: string;
  onDone: () => void;
}) {
  const [when, setWhen] = useState(defaultSlot());
  const [platforms, setPlatforms] = useState<Platform[]>([...PLATFORMS]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (p: Platform) =>
    setPlatforms((cur) => (cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p]));

  const submit = async () => {
    setPending(true);
    setError(null);
    try {
      await browserApiFetch(
        `/content/carousels/${encodeURIComponent(item.id)}/schedule?brand=${encodeURIComponent(brand)}`,
        {
          method: "POST",
          body: JSON.stringify({ scheduled_at: new Date(when).toISOString(), platforms }),
        },
      );
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="mt-3 space-y-2 rounded-lg border border-white/[0.06] bg-[var(--surface-canvas)] p-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-300">
        <label className="flex items-center gap-2">
          <CalendarClock className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only">Post time</span>
          <input
            type="datetime-local"
            aria-label="Post time"
            value={when}
            onChange={(e) => setWhen(e.target.value)}
            className="rounded-md border border-white/[0.1] bg-[var(--surface-inset)] px-2 py-1 text-slate-100"
          />
        </label>
        {PLATFORMS.map((p) => (
          <label key={p} className="flex items-center gap-1.5">
            <input type="checkbox" checked={platforms.includes(p)} onChange={() => toggle(p)} />
            {p === "INSTAGRAM" ? "Instagram" : "Facebook"}
          </label>
        ))}
        <button
          type="button"
          disabled={pending || platforms.length === 0 || !when}
          onClick={() => void submit()}
          className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          <Send className="h-4 w-4" aria-hidden="true" /> Schedule
        </button>
      </div>
      <p className="text-[11px] text-slate-500">
        Times are in your local time. Postiz publishes at that time; images are uploaded to your
        GitHub Pages media site first. Daily cap: 2 posts per platform.
      </p>
      {error ? <ErrorBanner title="Could not schedule" message={error} /> : null}
    </div>
  );
}

function Row({ item, brand, onChanged }: { item: CarouselSummary; brand: string; onChanged: () => void }) {
  const thumb = item.thumbnail_asset_id
    ? `/api/v1/content/assets/${encodeURIComponent(item.thumbnail_asset_id)}/file?brand=${encodeURIComponent(brand)}`
    : null;
  // The API decides (QA-passed, or every earlier platform post failed or was cancelled).
  const canSchedule = item.can_schedule && !item.scheduling;
  const rescheduling = canSchedule && item.schedules.length > 0;
  return (
    <div className="rounded-lg border border-white/[0.06] bg-[var(--surface-inset)] p-3">
      <div className="flex items-start gap-3">
        {thumb ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumb} alt="" className="h-24 w-20 flex-none rounded object-cover" />
        ) : (
          <div className="flex h-24 w-20 flex-none items-center justify-center rounded bg-[var(--surface-inset)]">
            <ImageOff className="h-4 w-4 text-slate-500" aria-hidden="true" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-slate-100">{item.hook ?? item.title}</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Badge tone="info">{LANGUAGE_LABEL[item.language ?? ""] ?? item.language ?? "—"}</Badge>
            <Badge tone={statusTone(item.status)}>{item.status}</Badge>
            {item.scheduling ? <Badge tone="prototype">Uploading & scheduling…</Badge> : null}
          </div>
          {item.schedules.length > 0 ? (
            <ul className="mt-2 space-y-1 text-xs text-slate-300">
              {item.schedules.map((s) => (
                <li key={`${s.platform}-${s.scheduled_at}`} className="flex flex-wrap items-center gap-2">
                  <Badge tone={statusTone(s.status)}>{s.status}</Badge>
                  <span>{s.platform === "INSTAGRAM" ? "Instagram" : "Facebook"}</span>
                  <span className="text-slate-500">{fmt(s.scheduled_at)}</span>
                  {s.post_url ? (
                    <a href={s.post_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-sky-300">
                      View post <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </a>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
      {rescheduling ? (
        <p className="mt-3 text-xs text-amber-300/90">
          The earlier post failed or was cancelled. Pick a new time to schedule it again.
        </p>
      ) : null}
      {canSchedule ? <ScheduleForm item={item} brand={brand} onDone={onChanged} /> : null}
    </div>
  );
}

export function PublishQueueView() {
  const { activeBrand } = useBrandContext();
  const brand = activeBrand.brand_id;
  const [items, setItems] = useState<CarouselSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    browserApiFetch<CarouselListResponse>(
      `/content/carousels?brand=${encodeURIComponent(brand)}&queue=publish`,
    )
      .then((res) => {
        if (!cancelled) {
          setError(null);
          setItems(res.items);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [brand, reloadKey]);

  return (
    <div className="space-y-4">
      <SurfaceCard
        icon={<Send />} title={`Approved — ready to post (${activeBrand.name})`}
        subtitle="Pick a time and channels. Nothing is posted until you press Schedule."
        badge={<Badge tone="live">Instagram + Facebook via Postiz</Badge>}
      >
        <div className="flex justify-end">
          <button
            type="button"
            onClick={reload}
            className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.1] px-2.5 py-1 text-xs text-slate-300"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" /> Refresh
          </button>
        </div>
      </SurfaceCard>
      {error ? <ErrorBanner title="Could not load approved carousels" message={error} /> : null}
      {items === null && !error ? <LoadingSkeleton /> : null}
      {items !== null && items.length === 0 ? (
        <EmptyState
          icon={<Send className="h-5 w-5" aria-hidden="true" />}
          title="No approved carousels yet"
          description="Approve carousels in the review queue and they appear here, ready to schedule."
          phaseBadge="N3 — publishing"
          tone="live"
          brandLabel={activeBrand.name}
        />
      ) : null}
      {items && items.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {items.map((item) => (
            <Row key={item.id} item={item} brand={brand} onChanged={reload} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
