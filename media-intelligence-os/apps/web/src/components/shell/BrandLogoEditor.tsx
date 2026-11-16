"use client";

import React, { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Camera, Loader2, Trash2 } from "lucide-react";

import { BrandLogo, brandLogoAssetId } from "@/components/shell/BrandLogo";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";
import type { BrandProfileSummary } from "@/lib/types";

const ACCEPT = "image/png,image/jpeg,image/webp";
const MAX_BYTES = 5 * 1024 * 1024;

/** Brand logo with a click-to-change overlay. Saves to the API, then refreshes brand data. */
export function BrandLogoEditor({
  brand,
  size = 64,
  showRemove = false,
}: {
  brand: BrandProfileSummary;
  size?: number;
  showRemove?: boolean;
}) {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [, startTransition] = useTransition();
  const editable = brand.data_origin === "postgresql";
  const hasLogo = brandLogoAssetId(brand) !== null;

  const done = () => startTransition(() => router.refresh());

  async function upload(file: File) {
    setError(null);
    if (!ACCEPT.split(",").includes(file.type)) {
      setError("Use a PNG, JPEG or WebP image.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setError("The logo can be up to 5 MB.");
      return;
    }
    setBusy(true);
    try {
      await browserApiFetch(`/brands/${encodeURIComponent(brand.brand_id)}/logo`, {
        method: "PUT",
        body: file,
        headers: { "Content-Type": file.type },
      });
      done();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setError(null);
    setBusy(true);
    try {
      await browserApiFetch(`/brands/${encodeURIComponent(brand.brand_id)}/logo`, { method: "DELETE" });
      done();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Couldn't remove the logo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        disabled={!editable || busy}
        onClick={() => input.current?.click()}
        aria-label={hasLogo ? `Change ${brand.name} logo` : `Add a logo for ${brand.name}`}
        title={editable ? (hasLogo ? "Change logo" : "Add your brand logo") : "Logos need the live database"}
        className="group relative shrink-0 rounded-2xl focus-visible:ring-2 focus-visible:ring-sky-400/60 focus-visible:outline-none disabled:cursor-default"
        style={{ width: size, height: size }}
      >
        <BrandLogo
          brand={brand}
          size={size}
          rounded="rounded-2xl"
          className={hasLogo ? "shadow-[0_8px_24px_-10px_rgba(0,0,0,0.8)]" : "border border-dashed border-white/20"}
        />
        {editable && (
          <span
            className={`absolute inset-0 flex flex-col items-center justify-center gap-0.5 rounded-2xl bg-black/55 text-[10px] font-medium text-white backdrop-blur-[2px] transition-opacity ${
              busy ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100"
            }`}
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <>
                <Camera className="h-4 w-4" aria-hidden="true" />
                {hasLogo ? "Change" : "Add logo"}
              </>
            )}
          </span>
        )}
      </button>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        className="hidden"
        data-testid="brand-logo-input"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void upload(file);
        }}
      />
      {showRemove && hasLogo && editable && (
        <button
          type="button"
          onClick={() => void remove()}
          disabled={busy}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-slate-400 hover:bg-white/[0.05] hover:text-rose-300"
        >
          <Trash2 className="h-3 w-3" aria-hidden="true" /> Remove
        </button>
      )}
      {error && (
        <p role="alert" className="text-[11px] text-rose-300">
          {error}
        </p>
      )}
    </div>
  );
}
