"use client";

import React, { useState } from "react";

import type { BrandProfileSummary } from "@/lib/types";

export function brandLogoAssetId(brand: Pick<BrandProfileSummary, "visual_identity">): string | null {
  const id = brand.visual_identity?.["logo_asset_id"];
  return typeof id === "string" && id ? id : null;
}

export function brandLogoUrl(brand: Pick<BrandProfileSummary, "visual_identity" | "brand_id">): string | null {
  const id = brandLogoAssetId(brand);
  return id
    ? `/api/v1/content/assets/${encodeURIComponent(id)}/file?brand=${encodeURIComponent(brand.brand_id)}`
    : null;
}

export function brandInitials(name: string): string {
  return name
    .split(/\s|&|\(/)
    .filter((w) => /^[A-Za-z0-9]/.test(w))
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/** The brand's uploaded logo, or its initials on a gradient tile when there is none. */
export function BrandLogo({
  brand,
  size = 24,
  rounded = "rounded-md",
  className = "",
}: {
  brand: Pick<BrandProfileSummary, "visual_identity" | "brand_id" | "name">;
  size?: number;
  rounded?: string;
  className?: string;
}) {
  const url = brandLogoUrl(brand);
  const [broken, setBroken] = useState<string | null>(null);
  const style = { width: size, height: size };
  if (url && broken !== url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
      <img
        src={url}
        alt=""
        aria-hidden="true"
        style={style}
        onError={() => setBroken(url)}
        className={`shrink-0 object-cover ring-1 ring-white/15 ${rounded} ${className}`}
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      style={{ ...style, fontSize: Math.max(9, Math.round(size * 0.38)) }}
      className={`flex shrink-0 items-center justify-center bg-gradient-to-br from-sky-300/30 to-indigo-400/25 font-semibold text-sky-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.15)] ${rounded} ${className}`}
    >
      {brandInitials(brand.name)}
    </span>
  );
}
