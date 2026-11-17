"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Film, FolderKanban, ImageIcon, Play, RefreshCw, Sparkles } from "lucide-react";

import { BrandLogoEditor } from "@/components/shell/BrandLogoEditor";
import { DesignTemplates } from "@/components/views/DesignTemplates";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner, LoadingSkeleton } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";
import type { LibraryItem, LibraryResponse } from "@/lib/types";

type Kind = "all" | "REEL_VIDEO" | "CAROUSEL_SLIDE" | "REEL_POSTER";

const TABS: ReadonlyArray<{ value: Kind; label: string }> = [
  { value: "all", label: "Everything" },
  { value: "REEL_VIDEO", label: "Reels" },
  { value: "CAROUSEL_SLIDE", label: "Carousel slides" },
  { value: "REEL_POSTER", label: "Reel covers" },
];

const KIND_LABEL: Record<string, string> = {
  REEL_VIDEO: "Reel",
  REEL_POSTER: "Cover",
  CAROUSEL_SLIDE: "Slide",
  BRAND_LOGO: "Logo",
};

function fileUrl(id: string, brand: string): string {
  return `/api/v1/content/assets/${encodeURIComponent(id)}/file?brand=${encodeURIComponent(brand)}`;
}

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function Tile({
  item,
  brand,
  posterFor,
}: {
  item: LibraryItem;
  brand: string;
  posterFor: (packageId: string | null) => string | null;
}) {
  const [playing, setPlaying] = useState(false);
  const isVideo = item.mime_type.startsWith("video/");
  const poster = isVideo ? posterFor(item.content_package_id) : null;
  return (
    <figure className="surface surface-interactive group overflow-hidden">
      <div className={`relative w-full bg-black/40 ${isVideo || item.kind === "REEL_POSTER" ? "aspect-[9/16]" : "aspect-[4/5]"}`}>
        {isVideo ? (
          playing ? (
            <video src={fileUrl(item.asset_id, brand)} controls autoPlay playsInline className="h-full w-full object-contain" />
          ) : (
            <button
              type="button"
              onClick={() => setPlaying(true)}
              aria-label={`Play ${item.title ?? "reel"}`}
              className="absolute inset-0 flex items-center justify-center"
            >
              {poster ? (
                // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
                <img src={fileUrl(poster, brand)} alt="" className="absolute inset-0 h-full w-full object-cover" />
              ) : (
                <Film className="h-7 w-7 text-slate-600" aria-hidden="true" />
              )}
              <span className="relative flex h-10 w-10 items-center justify-center rounded-full bg-black/55 text-white ring-1 ring-white/25 backdrop-blur transition-transform group-hover:scale-110">
                <Play className="ml-0.5 h-4 w-4" aria-hidden="true" />
              </span>
            </button>
          )
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
          <img src={fileUrl(item.asset_id, brand)} alt={item.title ?? ""} loading="lazy" className="absolute inset-0 h-full w-full object-cover" />
        )}
        <span className="absolute top-2 left-2">
          <Badge tone={isVideo ? "info" : "disabled"}>{KIND_LABEL[item.kind] ?? item.kind}</Badge>
        </span>
      </div>
      <figcaption className="space-y-0.5 px-3 py-2.5 text-[11px]">
        <p className="truncate font-medium text-slate-200">{item.title ?? "Untitled"}</p>
        <p className="text-slate-500">
          {item.width && item.height ? `${item.width}×${item.height} · ` : ""}
          {item.duration_seconds ? `${Math.round(item.duration_seconds)}s · ` : ""}
          {fmtSize(item.file_size)}
        </p>
      </figcaption>
    </figure>
  );
}

export function AssetsView() {
  const { activeBrand, buildHrefWithBrand } = useBrandContext();
  const brand = activeBrand.brand_id;
  const [items, setItems] = useState<LibraryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Kind>("all");
  const live = activeBrand.data_origin === "postgresql";

  const load = useCallback(() => {
    if (!live) return;
    browserApiFetch<LibraryResponse>(`/content/library?brand=${encodeURIComponent(brand)}`)
      .then((res) => {
        setItems(res.items);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof ApiClientError ? err.message : "Library unavailable."));
  }, [brand, live]);

  const [shownBrand, setShownBrand] = useState(brand);
  if (shownBrand !== brand) {
    setShownBrand(brand);
    setItems(null);
  }

  useEffect(() => {
    load();
  }, [load]);

  const posters = useMemo(() => {
    const m = new Map<string, string>();
    for (const i of items ?? []) {
      if (i.kind === "REEL_POSTER" && i.content_package_id && !m.has(i.content_package_id)) {
        m.set(i.content_package_id, i.asset_id);
      }
    }
    return m;
  }, [items]);

  const library = (items ?? []).filter((i) => i.kind !== "BRAND_LOGO");
  const counts = useMemo(() => {
    const c: Record<Kind, number> = { all: 0, REEL_VIDEO: 0, CAROUSEL_SLIDE: 0, REEL_POSTER: 0 };
    for (const i of library) {
      c.all += 1;
      if (i.kind in c) c[i.kind as Kind] += 1;
    }
    return c;
  }, [library]);
  const shown = library.filter((i) => tab === "all" || i.kind === tab);

  return (
    <div className="space-y-6">
      <SurfaceCard
        icon={<Sparkles />}
        title="Brand logo"
        subtitle="Shown beside the brand name in the workspace and in the brand switcher. PNG, JPEG or WebP, up to 5 MB; a square image works best."
      >
        <BrandLogoEditor brand={activeBrand} size={88} showRemove />
      </SurfaceCard>

      {live && <DesignTemplates brand={brand} />}

      <SurfaceCard
        icon={<FolderKanban />}
        title={`Asset library — ${activeBrand.name}`}
        subtitle="Every reel you've uploaded and every carousel slide Media OS has rendered for this brand."
        badge={
          live ? (
            <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-slate-400 hover:bg-white/[0.05] hover:text-slate-100">
              <RefreshCw className="h-3 w-3" aria-hidden="true" /> Refresh
            </button>
          ) : undefined
        }
      >
        <div className="mb-4 flex flex-wrap gap-1.5" role="tablist" aria-label="Asset type">
          {TABS.map((t) => (
            <button
              key={t.value}
              type="button"
              role="tab"
              aria-selected={tab === t.value}
              onClick={() => setTab(t.value)}
              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                tab === t.value
                  ? "border-sky-400/50 bg-sky-500/15 text-sky-200"
                  : "border-white/[0.1] bg-[var(--surface-inset)] text-slate-400 hover:text-slate-200"
              }`}
            >
              {t.label}
              <span className="ml-1.5 text-slate-500 tabular">{counts[t.value]}</span>
            </button>
          ))}
        </div>

        {error && <ErrorBanner title="Asset library" message={error} />}

        {!live ? (
          <EmptyState
            compact
            icon={<FolderKanban className="h-5 w-5" aria-hidden="true" />}
            title="The library needs the live database"
            description="Connect the dashboard to Media OS to browse this brand's files."
          />
        ) : items === null && !error ? (
          <LoadingSkeleton rows={2} />
        ) : shown.length === 0 ? (
          <EmptyState
            compact
            icon={<ImageIcon className="h-5 w-5" aria-hidden="true" />}
            title="Nothing here yet"
            description={
              <>
                Upload your reels in{" "}
                <Link href={buildHrefWithBrand("/studio")} className="text-sky-300 hover:underline">
                  Studio
                </Link>
                . Rendered carousel slides appear here automatically.
              </>
            }
          />
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
            {shown.map((i) => (
              <Tile key={i.asset_id} item={i} brand={brand} posterFor={(id) => (id ? (posters.get(id) ?? null) : null)} />
            ))}
          </div>
        )}
      </SurfaceCard>
    </div>
  );
}
