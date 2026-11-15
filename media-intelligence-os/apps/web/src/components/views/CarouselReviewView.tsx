"use client";

import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2, ImageOff, RefreshCw, ShieldCheck, XCircle } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner, LoadingSkeleton } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";
import type { CarouselDetail, CarouselListResponse, CarouselSummary, QaCheck } from "@/lib/types";

const LANGUAGE_LABEL: Record<string, string> = {
  BN: "Bengali",
  EN: "English",
  BN_EN: "Bilingual",
};

function checkTone(status: string): BadgeTone {
  if (status === "PASS") return "live";
  if (status === "WARN") return "prototype";
  return "blocked";
}

function errorText(err: unknown): string {
  return err instanceof ApiClientError ? `${err.message} (${err.code})` : "Request failed.";
}

function slideUrl(assetId: string, brand: string): string {
  return `/api/v1/content/assets/${encodeURIComponent(assetId)}/file?brand=${encodeURIComponent(brand)}`;
}

function QueueRow({
  item,
  brand,
  selected,
  onSelect,
}: {
  item: CarouselSummary;
  brand: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`flex w-full items-center gap-3 rounded-lg border p-3 text-left transition ${
        selected ? "border-amber-500/60 bg-amber-500/10" : "border-slate-800 bg-[var(--surface-inset)] hover:border-slate-600"
      }`}
    >
      {item.thumbnail_asset_id ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={slideUrl(item.thumbnail_asset_id, brand)}
          alt=""
          className="h-20 w-16 flex-none rounded object-cover"
        />
      ) : (
        <div className="flex h-20 w-16 flex-none items-center justify-center rounded bg-slate-900">
          <ImageOff className="h-4 w-4 text-slate-500" aria-hidden="true" />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-slate-100">{item.hook ?? item.title ?? "Carousel"}</p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <Badge tone="info">{LANGUAGE_LABEL[item.language ?? ""] ?? item.language ?? "—"}</Badge>
          <Badge tone={item.status === "QA_PASSED" ? "live" : "disabled"}>{item.status}</Badge>
          {item.score_total !== null ? <Badge tone="prototype">score {item.score_total}</Badge> : null}
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          {item.pillar} · {item.hook_type} · {item.slide_count} slides
        </p>
      </div>
    </button>
  );
}

function DetailPanel({
  detail,
  brand,
  onDecided,
}: {
  detail: CarouselDetail;
  brand: string;
  onDecided: () => void;
}) {
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canApprove = detail.status === "QA_PASSED" && detail.approval_status === "PENDING";

  const decide = async (decision: "APPROVE" | "REJECT") => {
    setPending(true);
    setError(null);
    try {
      await browserApiFetch(
        `/content/carousels/${encodeURIComponent(detail.id)}/decision?brand=${encodeURIComponent(brand)}`,
        { method: "POST", body: JSON.stringify({ decision, reason: reason || null }) },
      );
      setReason("");
      onDecided();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  };

  const recheck = async () => {
    setPending(true);
    setError(null);
    try {
      await browserApiFetch(
        `/content/carousels/${encodeURIComponent(detail.id)}/recheck?brand=${encodeURIComponent(brand)}`,
        { method: "POST" },
      );
      onDecided();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  };

  const checks: QaCheck[] = detail.qa_report?.checks ?? [];
  return (
    <div className="space-y-5">
      <div className="flex gap-3 overflow-x-auto pb-2" aria-label="Slides">
        {detail.assets.length === 0 && detail.qa_passed === false ? (
          <div className="space-y-2 rounded-lg border border-rose-900/60 bg-rose-950/30 p-3 text-xs text-rose-200">
            <p>
              This draft didn&apos;t pass the automated checks, so it wasn&apos;t rendered. See the
              failed checks below. You can reject it, or re-check it if the rules have changed since.
            </p>
            <button
              type="button"
              disabled={pending}
              onClick={() => void recheck()}
              className="rounded-md border border-rose-700 px-2.5 py-1 text-rose-100 disabled:opacity-40"
            >
              Re-check with latest rules
            </button>
          </div>
        ) : detail.assets.length === 0 ? (
          <p className="text-xs text-slate-400">Slides are still rendering — refresh in a moment.</p>
        ) : (
          detail.assets.map((asset) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={asset.id}
              src={slideUrl(asset.id, brand)}
              alt={`Slide ${asset.index}`}
              className="h-72 flex-none rounded-lg border border-slate-800"
            />
          ))
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Caption</p>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-[var(--surface-inset)] p-3 text-xs text-slate-200">
            {detail.caption}
          </pre>
          <p className="text-xs text-slate-400">{detail.hashtags.join(" ")}</p>
        </div>
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Score {detail.score?.total ?? "—"}
          </p>
          <p className="text-[11px] text-amber-300/80">{detail.score?.label}</p>
          {detail.score?.suggestion ? (
            <p className="text-xs text-slate-300">Suggestion: {detail.score.suggestion}</p>
          ) : null}
          <p className="pt-2 text-xs font-semibold uppercase tracking-wide text-slate-400">QA checks</p>
          <ul className="space-y-1">
            {checks.map((check) => (
              <li key={check.name} className="flex items-start gap-2 text-xs text-slate-300">
                <Badge tone={checkTone(check.status)}>{check.status}</Badge>
                <span>
                  {check.name}
                  {check.detail ? <span className="text-slate-500"> — {check.detail}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="space-y-2 rounded-lg border border-slate-800 bg-[var(--surface-inset)] p-4">
        <label htmlFor="decision-reason" className="text-xs font-semibold text-slate-300">
          Note (optional; required context for a rejection helps the next draft)
        </label>
        <textarea
          id="decision-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value.slice(0, 500))}
          rows={2}
          className="w-full rounded-md border border-slate-700 bg-[var(--surface-canvas)] p-2 text-sm text-slate-100"
        />
        {error ? <ErrorBanner title="Decision failed" message={error} /> : null}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={!canApprove || pending}
            onClick={() => void decide("APPROVE")}
            className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> Approve
          </button>
          <button
            type="button"
            disabled={detail.approval_status !== "PENDING" || pending}
            onClick={() => void decide("REJECT")}
            className="inline-flex items-center gap-1.5 rounded-md bg-rose-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            <XCircle className="h-4 w-4" aria-hidden="true" /> Reject
          </button>
        </div>
        <p className="text-[11px] text-slate-500">
          Approving records your decision only. Nothing is posted until publishing (N3) is set up.
        </p>
      </div>
    </div>
  );
}

export function CarouselReviewView() {
  const { activeBrand } = useBrandContext();
  const brand = activeBrand.brand_id;
  const [items, setItems] = useState<CarouselSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CarouselDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let cancelled = false;
    browserApiFetch<CarouselListResponse>(
      `/content/carousels?brand=${encodeURIComponent(brand)}&queue=review`,
    )
      .then((res) => {
        if (cancelled) return;
        setError(null);
        setItems(res.items);
        setSelectedId((current) =>
          current && res.items.some((i) => i.id === current) ? current : (res.items[0]?.id ?? null),
        );
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [brand, reloadKey]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    browserApiFetch<CarouselDetail>(
      `/content/carousels/${encodeURIComponent(selectedId)}?brand=${encodeURIComponent(brand)}`,
    )
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [brand, selectedId, reloadKey]);

  return (
    <div className="space-y-6">
      <SurfaceCard
        title={`Carousel review — ${activeBrand.name}`}
        subtitle="Every carousel needs your approval. Scores are a heuristic rubric, not a reach prediction."
        badge={<Badge tone="live">Human approval required</Badge>}
      >
        <div className="flex justify-end">
          <button
            type="button"
            onClick={reload}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-300"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" /> Refresh
          </button>
        </div>
      </SurfaceCard>

      {error ? <ErrorBanner title="Could not load the review queue" message={error} /> : null}
      {items === null && !error ? <LoadingSkeleton /> : null}
      {items !== null && items.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck className="h-5 w-5" aria-hidden="true" />}
          title={`0 carousels awaiting review for ${activeBrand.name}`}
          description="Generated carousels appear here after rendering and automated QA."
          phaseBadge="N2 — review queue"
          tone="live"
          brandLabel={activeBrand.name}
        />
      ) : null}
      {items && items.length > 0 ? (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[22rem_1fr]">
          <div className="space-y-2">
            {items.map((item) => (
              <QueueRow
                key={item.id}
                item={item}
                brand={brand}
                selected={item.id === selectedId}
                onSelect={() => setSelectedId(item.id)}
              />
            ))}
          </div>
          <SurfaceCard title={detail?.hook ?? "Select a carousel"} subtitle={detail?.alt_text ?? ""}>
            {detail && detail.id === selectedId ? (
              <DetailPanel detail={detail} brand={brand} onDecided={reload} />
            ) : (
              <LoadingSkeleton />
            )}
          </SurfaceCard>
        </div>
      ) : null}
    </div>
  );
}
