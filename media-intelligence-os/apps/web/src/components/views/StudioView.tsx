"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  CalendarClock,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Film,
  ImageIcon,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Dropdown } from "@/components/ui/Dropdown";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner, LoadingSkeleton } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { AutopilotPanel } from "@/components/views/AutopilotPanel";
import { defaultSlot } from "@/components/views/PublishQueueView";
import { ApiClientError, browserApiFetch, readCsrfCookie } from "@/lib/api-client";
import { useBrandContext } from "@/lib/brand-context";
import type { BatchScheduleResponse, ReelItem, ReelListResponse } from "@/lib/types";

const ACCEPT = "video/mp4,video/quicktime,.mp4,.mov,image/png,image/jpeg,image/webp";
const MAX_BYTES = 95 * 1024 * 1024;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_SLIDES = 10;

export const FORMAT_LABEL: Record<string, string> = { REEL: "Reel", CAROUSEL: "Carousel", POST: "Photo" };
const CAPTION_MAX = 2200;
const PLATFORMS = ["INSTAGRAM", "FACEBOOK"] as const;
type Platform = (typeof PLATFORMS)[number];

type Filter = "review" | "ready" | "scheduled" | "all";

const SPACING: ReadonlyArray<{ value: string; label: string }> = [
  { value: "1440", label: "One a day" },
  { value: "720", label: "Every 12 hours" },
  { value: "2880", label: "Every 2 days" },
  { value: "360", label: "Every 6 hours" },
  { value: "180", label: "Every 3 hours" },
];

function errorText(err: unknown): string {
  return err instanceof ApiClientError ? err.message : "Request failed.";
}

function assetUrl(assetId: string, brand: string): string {
  return `/api/v1/content/assets/${encodeURIComponent(assetId)}/file?brand=${encodeURIComponent(brand)}`;
}

function fmtDuration(s: number | null): string {
  if (!s) return "—";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m ? `${m}:${String(sec).padStart(2, "0")}` : `${sec}s`;
}

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** Where a reel is in its life, in plain words. */
export function reelStage(r: ReelItem): { label: string; tone: BadgeTone; group: Exclude<Filter, "all"> } {
  const live = r.schedules.filter((s) => s.status !== "FAILED" && s.status !== "CANCELLED");
  if (r.status === "PUBLISHED" || live.some((s) => s.status === "PUBLISHED")) {
    return { label: "Published", tone: "live", group: "scheduled" };
  }
  if (r.scheduling) return { label: "Scheduling…", tone: "info", group: "scheduled" };
  if (live.length) return { label: "Scheduled", tone: "info", group: "scheduled" };
  if (r.approval_status === "APPROVED") {
    return r.can_schedule
      ? { label: r.schedules.length ? "Failed · reschedule" : "Approved", tone: r.schedules.length ? "blocked" : "live", group: "ready" }
      : { label: "Approved", tone: "live", group: "ready" };
  }
  if (r.qa_passed === false) return { label: "Can't be posted", tone: "blocked", group: "review" };
  return { label: "Needs review", tone: "prototype", group: "review" };
}

/* ------------------------------------------------------------------ uploads */

interface UploadTask {
  key: string;
  name: string;
  progress: number; // 0..1
  state: "uploading" | "processing" | "done" | "error";
  error?: string;
}

/** Grab a frame (~1 s in) as a JPEG cover. Returns null if the browser can't decode the video. */
export function capturePoster(file: File, timeoutMs = 10000): Promise<Blob | null> {
  return new Promise((resolve) => {
    if (typeof document === "undefined" || typeof URL.createObjectURL !== "function") {
      resolve(null);
      return;
    }
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    let finished = false;
    const finish = (blob: Blob | null) => {
      if (finished) return;
      finished = true;
      URL.revokeObjectURL(url);
      resolve(blob);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.onerror = () => finish(null);
    video.onloadedmetadata = () => {
      video.currentTime = Math.min(1, (video.duration || 2) / 3);
    };
    video.onseeked = () => {
      try {
        const w = 540;
        const h = Math.round((video.videoHeight / Math.max(1, video.videoWidth)) * w) || 960;
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        canvas.getContext("2d")?.drawImage(video, 0, 0, w, h);
        canvas.toBlob(
          (b) => {
            clearTimeout(timer);
            finish(b);
          },
          "image/jpeg",
          0.82,
        );
      } catch {
        clearTimeout(timer);
        finish(null);
      }
    };
    video.src = url;
  });
}

function uploadFile(
  file: File,
  brand: string,
  onProgress: (p: number) => void,
): Promise<ReelItem> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const q = new URLSearchParams({ brand, filename: file.name });
    xhr.open("POST", `/api/v1/content/reels/upload?${q.toString()}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", file.type || "video/mp4");
    const csrf = readCsrfCookie();
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onerror = () => reject(new Error("Upload failed: network error."));
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as ReelItem);
      } else {
        const msg = (body as { error?: { message?: string } } | null)?.error?.message;
        reject(new Error(msg ?? `Upload failed (HTTP ${xhr.status}).`));
      }
    };
    xhr.send(file);
  });
}

interface PendingImage {
  key: string;
  file: File;
  url: string;
}

/** Move item i by `by` places; used to set carousel slide order. */
export function moveItem<T>(list: T[], i: number, by: number): T[] {
  const j = i + by;
  if (j < 0 || j >= list.length) return list;
  const next = [...list];
  [next[i], next[j]] = [next[j]!, next[i]!];
  return next;
}

/** Picked images, in order: make one carousel (2-10) or a separate photo post from each. */
function ImageTray({
  brand,
  images,
  setImages,
  onCreated,
}: {
  brand: string;
  images: PendingImage[];
  setImages: React.Dispatch<React.SetStateAction<PendingImage[]>>;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const n = images.length;

  const drop = (keys: string[]) =>
    setImages((cur) => {
      for (const img of cur) if (keys.includes(img.key) && img.url) URL.revokeObjectURL(img.url);
      return cur.filter((img) => !keys.includes(img.key));
    });

  async function stage(img: PendingImage): Promise<string> {
    const res = await browserApiFetch<{ asset_id: string }>(
      `/content/images/upload?brand=${encodeURIComponent(brand)}`,
      { method: "POST", body: img.file, headers: { "Content-Type": img.file.type || "image/jpeg" } },
    );
    return res.asset_id;
  }

  async function create(mode: "carousel" | "separate") {
    setError(null);
    const q = `?brand=${encodeURIComponent(brand)}`;
    try {
      if (mode === "carousel") {
        const ids: string[] = [];
        for (const [i, img] of images.entries()) {
          setBusy(`Uploading ${i + 1} of ${n}…`);
          ids.push(await stage(img));
        }
        setBusy("Creating carousel…");
        await browserApiFetch(`/content/posts${q}`, {
          method: "POST",
          body: JSON.stringify({ asset_ids: ids, title: title.trim() }),
        });
        drop(images.map((i) => i.key));
      } else {
        for (const [i, img] of images.entries()) {
          setBusy(`Posting ${i + 1} of ${n}…`);
          const id = await stage(img);
          await browserApiFetch(`/content/posts${q}`, {
            method: "POST",
            body: JSON.stringify({ asset_ids: [id], title: img.file.name.replace(/\.[^.]+$/, "") }),
          });
          drop([img.key]);
        }
      }
      setTitle("");
      onCreated();
    } catch (err) {
      setError(errorText(err));
      onCreated();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-white/[0.08] bg-[var(--surface-inset)] p-3.5" aria-label="Images to post">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <p className="font-medium text-slate-100">
          {n} image{n === 1 ? "" : "s"} selected
          {n > 1 && <span className="ml-1 font-normal text-slate-400">· arrows set the carousel order</span>}
        </p>
        <button type="button" onClick={() => drop(images.map((i) => i.key))} disabled={busy !== null} className="text-slate-500 hover:text-slate-200">
          Clear
        </button>
      </div>
      <ol className="flex gap-2 overflow-x-auto pb-1">
        {images.map((img, i) => (
          <li key={img.key} className="relative w-24 shrink-0">
            {/* eslint-disable-next-line @next/next/no-img-element -- local preview */}
            <img src={img.url} alt={img.file.name} className="aspect-[4/5] w-24 rounded-md object-cover ring-1 ring-white/10" />
            <span className="absolute top-1 left-1 rounded bg-black/65 px-1.5 text-[10px] font-semibold text-white tabular">{i + 1}</span>
            <button
              type="button"
              onClick={() => drop([img.key])}
              disabled={busy !== null}
              aria-label={`Remove ${img.file.name}`}
              className="absolute top-1 right-1 rounded bg-black/65 p-0.5 text-white hover:bg-rose-500/80"
            >
              <X className="h-3 w-3" aria-hidden="true" />
            </button>
            {n > 1 && (
              <div className="mt-1 flex justify-between">
                <button
                  type="button"
                  onClick={() => setImages((cur) => moveItem(cur, i, -1))}
                  disabled={i === 0 || busy !== null}
                  aria-label={`Move ${img.file.name} earlier`}
                  className="rounded p-0.5 text-slate-400 hover:bg-white/[0.06] hover:text-slate-100 disabled:opacity-30"
                >
                  <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={() => setImages((cur) => moveItem(cur, i, 1))}
                  disabled={i === n - 1 || busy !== null}
                  aria-label={`Move ${img.file.name} later`}
                  className="rounded p-0.5 text-slate-400 hover:bg-white/[0.06] hover:text-slate-100 disabled:opacity-30"
                >
                  <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
            )}
          </li>
        ))}
      </ol>
      {n >= 2 && n <= MAX_SLIDES && (
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Carousel title (only for you)"
          aria-label="Carousel title"
          className="w-full rounded-md border border-white/[0.08] bg-[var(--surface-inset)] px-2.5 py-1.5 text-xs text-slate-100 focus:border-sky-400/60 focus:outline-none"
        />
      )}
      {n > MAX_SLIDES && (
        <p className="text-xs text-amber-300/90">A carousel takes up to 10 images. Remove some, or post them separately.</p>
      )}
      {error && <p role="alert" className="text-xs text-rose-300">{error}</p>}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {n >= 2 && n <= MAX_SLIDES && (
          <button
            type="button"
            onClick={() => void create("carousel")}
            disabled={busy !== null}
            className="btn-press inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-b from-sky-300 to-sky-400 px-3 py-1.5 font-semibold text-[#04121a] disabled:opacity-60"
          >
            <Layers className="h-3.5 w-3.5" aria-hidden="true" /> Make one carousel ({n})
          </button>
        )}
        <button
          type="button"
          onClick={() => void create("separate")}
          disabled={busy !== null}
          className={`btn-press inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium disabled:opacity-60 ${
            n === 1
              ? "bg-gradient-to-b from-sky-300 to-sky-400 font-semibold text-[#04121a]"
              : "glass-chip text-slate-100"
          }`}
        >
          <ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />
          {n === 1 ? "Create photo post" : `Post each separately (${n})`}
        </button>
        {busy && (
          <span className="inline-flex items-center gap-1.5 text-slate-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> {busy}
          </span>
        )}
      </div>
    </div>
  );
}

function UploadZone({ brand, onUploaded }: { brand: string; onUploaded: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [tasks, setTasks] = useState<UploadTask[]>([]);
  const [dragging, setDragging] = useState(false);

  const patch = (key: string, p: Partial<UploadTask>) =>
    setTasks((ts) => ts.map((t) => (t.key === key ? { ...t, ...p } : t)));

  const [images, setImages] = useState<PendingImage[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);

  async function handle(all: File[]) {
    const isImage = (f: File) => IMAGE_TYPES.includes(f.type) || /\.(png|jpe?g|webp)$/i.test(f.name);
    const picked = all.filter(isImage);
    if (picked.length) {
      setImageError(null);
      const ok = picked.filter((f) => f.size <= MAX_IMAGE_BYTES);
      if (ok.length < picked.length) setImageError("Images over 8 MB were skipped.");
      setImages((cur) =>
        [
          ...cur,
          ...ok.map((f, i) => ({
            key: `${Date.now()}-${i}-${f.name}`,
            file: f,
            url: typeof URL.createObjectURL === "function" ? URL.createObjectURL(f) : "",
          })),
        ].slice(0, 30),
      );
    }
    const files = all.filter((f) => !isImage(f));
    if (!files.length) return;
    const fresh = files.map((f, i) => ({
      file: f,
      task: { key: `${Date.now()}-${i}-${f.name}`, name: f.name, progress: 0, state: "uploading" as const },
    }));
    setTasks((ts) => [...fresh.map((x) => x.task), ...ts].slice(0, 30));
    // One at a time: keeps memory low on both ends and the progress readable.
    for (const { file, task } of fresh) {
      if (!/\.(mp4|mov)$/i.test(file.name) && !file.type.startsWith("video/")) {
        patch(task.key, { state: "error", error: "Use MP4 videos, or PNG, JPEG or WebP images." });
        continue;
      }
      if (file.size > MAX_BYTES) {
        patch(task.key, { state: "error", error: "Larger than 95 MB. Export it at a lower bitrate." });
        continue;
      }
      try {
        const reel = await uploadFile(file, brand, (p) => patch(task.key, { progress: p }));
        patch(task.key, { state: "processing", progress: 1 });
        const poster = await capturePoster(file);
        if (poster) {
          await browserApiFetch(`/content/reels/${reel.id}/poster?brand=${encodeURIComponent(brand)}`, {
            method: "PUT",
            body: poster,
            headers: { "Content-Type": "image/jpeg" },
          }).catch(() => undefined);
        }
        patch(task.key, { state: "done" });
        onUploaded();
      } catch (err) {
        patch(task.key, { state: "error", error: err instanceof Error ? err.message : "Upload failed." });
      }
    }
  }

  const active = tasks.filter((t) => t.state === "uploading" || t.state === "processing").length;

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handle(Array.from(e.dataTransfer.files));
        }}
        className={`flex w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-8 text-center transition-colors ${
          dragging
            ? "border-sky-400/70 bg-sky-400/[0.08]"
            : "border-white/[0.14] bg-[var(--surface-inset)] hover:border-sky-400/40 hover:bg-white/[0.04]"
        }`}
      >
        <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-sky-400/25 bg-sky-400/10 text-sky-300">
          <UploadCloud className="h-5 w-5" aria-hidden="true" />
        </span>
        <span className="text-sm font-medium text-slate-100">Upload reels, photos or carousel slides</span>
        <span className="max-w-xl text-xs text-slate-400">
          Drop files here or click to choose. Reels: MP4 up to 95 MB, 3 s to 15 min, 9:16 looks best. Photos: PNG, JPEG
          or WebP up to 8 MB; you then choose one carousel or separate posts.
        </span>
      </button>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        multiple
        className="hidden"
        data-testid="reel-file-input"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) void handle(files);
        }}
      />
      {imageError && <p className="text-xs text-amber-300/90">{imageError}</p>}
      {images.length > 0 && (
        <ImageTray brand={brand} images={images} setImages={setImages} onCreated={onUploaded} />
      )}
      {tasks.length > 0 && (
        <ul className="space-y-1.5" aria-label="Uploads">
          {tasks.map((t) => (
            <li key={t.key} className="rounded-lg border border-white/[0.06] bg-[var(--surface-inset)] px-3 py-2 text-xs">
              <div className="flex items-center gap-2">
                {t.state === "done" ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" aria-hidden="true" />
                ) : t.state === "error" ? (
                  <X className="h-3.5 w-3.5 text-rose-300" aria-hidden="true" />
                ) : (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-300" aria-hidden="true" />
                )}
                <span className="min-w-0 flex-1 truncate text-slate-200">{t.name}</span>
                <span className="text-slate-500">
                  {t.state === "uploading"
                    ? `${Math.round(t.progress * 100)}%`
                    : t.state === "processing"
                      ? "Making cover…"
                      : t.state === "done"
                        ? "Added"
                        : "Failed"}
                </span>
              </div>
              {t.state === "uploading" && (
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.06]">
                  <div className="h-full rounded-full bg-sky-400 transition-[width]" style={{ width: `${t.progress * 100}%` }} />
                </div>
              )}
              {t.error && <p className="mt-1 text-rose-300">{t.error}</p>}
            </li>
          ))}
        </ul>
      )}
      {active === 0 && tasks.some((t) => t.state !== "uploading") && (
        <button type="button" onClick={() => setTasks([])} className="text-[11px] text-slate-500 hover:text-slate-300">
          Clear list
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ one reel */

function ReelCard({
  reel,
  brand,
  selected,
  onSelect,
  onChanged,
}: {
  reel: ReelItem;
  brand: string;
  selected: boolean;
  onSelect: (on: boolean) => void;
  onChanged: () => void;
}) {
  const stage = reelStage(reel);
  const editable = reel.approval_status === "PENDING";
  const isVideo = (reel.format ?? "REEL") === "REEL";
  const slides = reel.slide_asset_ids ?? [];
  const noun = isVideo ? "reel" : reel.format === "CAROUSEL" ? "carousel" : "post";
  const [slide, setSlide] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [title, setTitle] = useState(reel.title ?? "");
  const [caption, setCaption] = useState(reel.caption ?? "");
  const [tags, setTags] = useState((reel.hashtags ?? []).join(" "));
  const [busy, setBusy] = useState<null | "save" | "approve" | "remove">(null);
  const [error, setError] = useState<string | null>(null);
  const dirty =
    title !== (reel.title ?? "") || caption !== (reel.caption ?? "") || tags !== (reel.hashtags ?? []).join(" ");
  const fullLength = caption.trim().length + (tags.trim() ? tags.trim().length + 2 : 0);
  const problems = (reel.qa_report?.checks ?? []).filter((c) => c.status !== "PASS");

  async function save(): Promise<boolean> {
    if (!dirty) return true;
    setBusy("save");
    setError(null);
    try {
      await browserApiFetch(`/content/studio/${reel.id}?brand=${encodeURIComponent(brand)}`, {
        method: "PATCH",
        body: JSON.stringify({ title, caption, hashtags: tags.split(/[\s,]+/).filter(Boolean) }),
      });
      onChanged();
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function approve() {
    if (!(await save())) return;
    setBusy("approve");
    setError(null);
    try {
      await browserApiFetch(`/content/carousels/${reel.id}/decision?brand=${encodeURIComponent(brand)}`, {
        method: "POST",
        body: JSON.stringify({ decision: "APPROVE" }),
      });
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!window.confirm(`Remove "${reel.title ?? `this ${noun}`}"? Its files are deleted.`)) return;
    setBusy("remove");
    setError(null);
    try {
      await browserApiFetch(`/content/studio/${reel.id}?brand=${encodeURIComponent(brand)}`, { method: "DELETE" });
      onChanged();
    } catch (err) {
      setError(errorText(err));
      setBusy(null);
    }
  }

  const live = reel.schedules.filter((s) => s.status !== "FAILED" && s.status !== "CANCELLED");

  return (
    <article
      className={`surface flex flex-col overflow-hidden transition-shadow ${
        selected ? "ring-2 ring-sky-400/60" : ""
      }`}
      aria-label={reel.title ?? FORMAT_LABEL[reel.format ?? "REEL"]}
    >
      <div
        className={`relative w-full bg-black/40 ${isVideo ? "aspect-[9/16] max-h-[22rem]" : "aspect-[4/5] max-h-[22rem]"}`}
      >
        {!isVideo ? (
          slides.length > 0 ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element -- authenticated API image */}
              <img
                src={assetUrl(slides[Math.min(slide, slides.length - 1)]!, brand)}
                alt={`${reel.title ?? "Post"}, image ${slide + 1} of ${slides.length}`}
                className="absolute inset-0 h-full w-full object-cover"
              />
              {slides.length > 1 && (
                <>
                  <button
                    type="button"
                    onClick={() => setSlide((s) => Math.max(0, s - 1))}
                    disabled={slide === 0}
                    aria-label="Previous image"
                    className="absolute top-1/2 left-2 -translate-y-1/2 rounded-full bg-black/55 p-1.5 text-white ring-1 ring-white/20 backdrop-blur disabled:opacity-0"
                  >
                    <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setSlide((s) => Math.min(slides.length - 1, s + 1))}
                    disabled={slide >= slides.length - 1}
                    aria-label="Next image"
                    className="absolute top-1/2 right-2 -translate-y-1/2 rounded-full bg-black/55 p-1.5 text-white ring-1 ring-white/20 backdrop-blur disabled:opacity-0"
                  >
                    <ChevronRight className="h-4 w-4" aria-hidden="true" />
                  </button>
                </>
              )}
            </>
          ) : (
            <span className="absolute inset-0 flex items-center justify-center">
              <ImageIcon className="h-8 w-8 text-slate-600" aria-hidden="true" />
            </span>
          )
        ) : playing && reel.video_asset_id ? (
          <video
            src={assetUrl(reel.video_asset_id, brand)}
            controls
            autoPlay
            playsInline
            className="h-full w-full object-contain"
          />
        ) : (
          <button
            type="button"
            onClick={() => setPlaying(true)}
            disabled={!reel.video_asset_id}
            className="group absolute inset-0 flex items-center justify-center"
            aria-label={`Play ${reel.title ?? "reel"}`}
          >
            {reel.thumbnail_asset_id ? (
              // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
              <img
                src={assetUrl(reel.thumbnail_asset_id, brand)}
                alt=""
                className="absolute inset-0 h-full w-full object-cover"
              />
            ) : (
              <Film className="h-8 w-8 text-slate-600" aria-hidden="true" />
            )}
            <span className="relative flex h-12 w-12 items-center justify-center rounded-full bg-black/55 text-white ring-1 ring-white/25 backdrop-blur transition-transform group-hover:scale-110">
              <Play className="ml-0.5 h-5 w-5" aria-hidden="true" />
            </span>
          </button>
        )}
        <label className="absolute top-2 left-2 flex items-center gap-1.5 rounded-md bg-black/55 px-2 py-1 text-[11px] text-slate-100 backdrop-blur">
          <input
            type="checkbox"
            checked={selected}
            onChange={(e) => onSelect(e.target.checked)}
            className="h-3.5 w-3.5 accent-sky-400"
            aria-label={`Select ${reel.title ?? noun}`}
          />
          Select
        </label>
        <span className="absolute top-2 right-2">
          <Badge tone={stage.tone}>{stage.label}</Badge>
        </span>
        <span className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-slate-100">
          {FORMAT_LABEL[reel.format ?? "REEL"] ?? reel.format}
        </span>
        <span className="absolute right-2 bottom-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-slate-200 tabular">
          {isVideo
            ? fmtDuration(reel.duration_seconds)
            : slides.length > 1
              ? `${Math.min(slide, slides.length - 1) + 1}/${slides.length}`
              : "1 image"}
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-2.5 p-3.5 text-xs">
        {editable ? (
          <>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              aria-label="Title (only for you)"
              placeholder="Title (only for you)"
              className="rounded-md border border-white/[0.08] bg-[var(--surface-inset)] px-2.5 py-1.5 font-medium text-slate-100 focus:border-sky-400/60 focus:outline-none"
            />
            <textarea
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              aria-label="Caption"
              placeholder="Caption for Instagram and Facebook"
              rows={4}
              className="resize-y rounded-md border border-white/[0.08] bg-[var(--surface-inset)] px-2.5 py-1.5 leading-relaxed text-slate-200 focus:border-sky-400/60 focus:outline-none"
            />
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              aria-label="Hashtags"
              placeholder="#wedding #biodata"
              className="rounded-md border border-white/[0.08] bg-[var(--surface-inset)] px-2.5 py-1.5 text-sky-200 focus:border-sky-400/60 focus:outline-none"
            />
            <p className={`text-[10.5px] ${fullLength > CAPTION_MAX ? "text-rose-300" : "text-slate-500"}`}>
              {fullLength}/{CAPTION_MAX} characters
            </p>
          </>
        ) : (
          <>
            <p className="font-medium text-slate-100">{reel.title}</p>
            <p className="line-clamp-4 whitespace-pre-line text-slate-400">{reel.caption || "No caption"}</p>
            {reel.hashtags.length > 0 && <p className="line-clamp-2 text-sky-300/80">{reel.hashtags.join(" ")}</p>}
          </>
        )}

        {problems.length > 0 && (
          <ul className="space-y-1">
            {problems.map((c) => (
              <li key={c.name} className={c.status === "FAIL" ? "text-rose-300" : "text-amber-300/90"}>
                {c.detail || c.name}
              </li>
            ))}
          </ul>
        )}

        {live.length > 0 && (
          <ul className="space-y-1 text-slate-400">
            {live.map((s) => (
              <li key={s.platform} className="flex items-center justify-between gap-2">
                <span>{s.platform === "INSTAGRAM" ? "Instagram" : "Facebook"}</span>
                {s.post_url ? (
                  <a href={s.post_url} target="_blank" rel="noreferrer" className="text-sky-300 hover:underline">
                    View post
                  </a>
                ) : (
                  <span className="text-slate-300">{fmtWhen(s.scheduled_at)}</span>
                )}
              </li>
            ))}
          </ul>
        )}

        {error && (
          <p role="alert" className="text-rose-300">
            {error}
          </p>
        )}

        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
          {editable && (
            <>
              <button
                type="button"
                onClick={() => void approve()}
                disabled={busy !== null || reel.qa_passed === false || fullLength > CAPTION_MAX}
                className="btn-press inline-flex items-center gap-1.5 rounded-md bg-gradient-to-b from-sky-300 to-sky-400 px-3 py-1.5 font-semibold text-[#04121a] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy === "approve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />}
                Approve
              </button>
              {dirty && (
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={busy !== null}
                  className="rounded-md border border-white/[0.12] px-3 py-1.5 text-slate-200 hover:bg-white/[0.05]"
                >
                  {busy === "save" ? "Saving…" : "Save"}
                </button>
              )}
            </>
          )}
          {live.length === 0 && !reel.scheduling && (
            <button
              type="button"
              onClick={() => void remove()}
              disabled={busy !== null}
              className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-slate-500 hover:bg-white/[0.05] hover:text-rose-300"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Remove
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ batch scheduling */

/** Evenly spaced times starting at `first` (datetime-local), one per item. */
export function planSlots(first: string, everyMinutes: number, count: number): Date[] {
  const start = new Date(first);
  if (Number.isNaN(start.getTime())) return [];
  return Array.from({ length: count }, (_, i) => new Date(start.getTime() + i * everyMinutes * 60_000));
}

function BatchScheduler({
  reels,
  brand,
  onClose,
  onDone,
}: {
  reels: ReelItem[];
  brand: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [first, setFirst] = useState(() => defaultSlot());
  const [every, setEvery] = useState("1440");
  const [platforms, setPlatforms] = useState<Platform[]>(["INSTAGRAM", "FACEBOOK"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BatchScheduleResponse | null>(null);
  const slots = useMemo(() => planSlots(first, Number(every), reels.length), [first, every, reels.length]);
  const titleOf = (id: string) => reels.find((r) => r.id === id)?.title ?? "Post";

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await browserApiFetch<BatchScheduleResponse>(
        `/content/schedule-batch?brand=${encodeURIComponent(brand)}`,
        {
          method: "POST",
          body: JSON.stringify({
            platforms,
            items: reels.map((r, i) => ({ package_id: r.id, scheduled_at: slots[i]!.toISOString() })),
          }),
        },
      );
      setResult(res);
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SurfaceCard
      icon={<CalendarClock />}
      title={`Schedule ${reels.length} post${reels.length === 1 ? "" : "s"}`}
      subtitle="Postiz publishes each one at its time. Your posts-per-day limit (set in Autopilot) and 5-minute spacing apply."
      badge={
        <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 text-slate-400 hover:bg-white/[0.06] hover:text-slate-100">
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      }
    >
      {result ? (
        <div className="space-y-3 text-xs">
          <p className="text-sm text-slate-100">
            {result.scheduled} of {result.results.length} scheduled.
          </p>
          <ul className="space-y-1.5">
            {result.results.map((r) => (
              <li key={r.package_id} className="flex flex-wrap items-center gap-2 rounded-md bg-[var(--surface-inset)] px-3 py-2">
                {r.ok ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" aria-hidden="true" /> : <X className="h-3.5 w-3.5 text-rose-300" aria-hidden="true" />}
                <span className="min-w-0 flex-1 truncate text-slate-200">{titleOf(r.package_id)}</span>
                <span className="text-slate-400">{fmtWhen(r.scheduled_at)}</span>
                {!r.ok && <span className="w-full text-rose-300">{r.message}</span>}
              </li>
            ))}
          </ul>
          <button type="button" onClick={onClose} className="rounded-md border border-white/[0.12] px-3 py-1.5 text-slate-200 hover:bg-white/[0.05]">
            Done
          </button>
        </div>
      ) : (
        <div className="space-y-4 text-xs">
          <div className="flex flex-wrap items-end gap-4">
            <label className="flex flex-col gap-1">
              <span className="text-[11px] font-medium text-slate-500">First post</span>
              <input
                type="datetime-local"
                value={first}
                onChange={(e) => setFirst(e.target.value)}
                className="h-8 rounded-lg border border-white/[0.08] bg-[var(--surface-inset)] px-2.5 text-slate-100 [color-scheme:dark] focus:border-sky-400/60 focus:outline-none"
              />
            </label>
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-medium text-slate-500">Then</span>
              <Dropdown ariaLabel="Spacing between posts" value={every} onChange={setEvery} options={SPACING} />
            </div>
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-[11px] font-medium text-slate-500">Post to</legend>
              <div className="flex gap-3">
                {PLATFORMS.map((p) => (
                  <label key={p} className="flex items-center gap-1.5 text-slate-200">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-sky-400"
                      checked={platforms.includes(p)}
                      onChange={(e) =>
                        setPlatforms((cur) => (e.target.checked ? [...cur, p] : cur.filter((x) => x !== p)))
                      }
                    />
                    {p === "INSTAGRAM" ? "Instagram" : "Facebook"}
                  </label>
                ))}
              </div>
            </fieldset>
          </div>
          <ol className="space-y-1">
            {reels.map((r, i) => (
              <li key={r.id} className="flex items-center gap-2 rounded-md bg-[var(--surface-inset)] px-3 py-1.5">
                <span className="w-5 text-slate-500 tabular">{i + 1}.</span>
                <span className="min-w-0 flex-1 truncate text-slate-200">{r.title}</span>
                <span className="text-slate-300 tabular">{slots[i] ? fmtWhen(slots[i]!.toISOString()) : "—"}</span>
              </li>
            ))}
          </ol>
          {error && <p role="alert" className="text-rose-300">{error}</p>}
          <button
            type="button"
            onClick={() => void submit()}
            disabled={busy || platforms.length === 0 || slots.length !== reels.length}
            className="btn-press inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-b from-sky-300 to-sky-400 px-3.5 py-2 font-semibold text-[#04121a] disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Send className="h-3.5 w-3.5" aria-hidden="true" />}
            Schedule {reels.length}
          </button>
        </div>
      )}
    </SurfaceCard>
  );
}

/* ------------------------------------------------------------------ page */

const FILTERS: ReadonlyArray<{ value: Filter; label: string }> = [
  { value: "review", label: "Needs review" },
  { value: "ready", label: "Ready to schedule" },
  { value: "scheduled", label: "Scheduled & published" },
  { value: "all", label: "All" },
];

export function StudioView() {
  const { activeBrand, buildHrefWithBrand } = useBrandContext();
  const brand = activeBrand.brand_id;
  const [reels, setReels] = useState<ReelItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("review");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scheduling, setScheduling] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);

  const load = useCallback(() => {
    browserApiFetch<ReelListResponse>(`/content/studio?brand=${encodeURIComponent(brand)}`)
      .then((res) => {
        setReels(res.items);
        setError(null);
      })
      .catch((err: unknown) => setError(errorText(err)));
  }, [brand]);

  // Reset when the brand changes (during render, not in an effect).
  const [shownBrand, setShownBrand] = useState(brand);
  if (shownBrand !== brand) {
    setShownBrand(brand);
    setReels(null);
    setSelected(new Set());
  }

  useEffect(() => {
    load();
  }, [load]);

  // Keep "Scheduling…" cards fresh until the worker finishes.
  useEffect(() => {
    if (!reels?.some((r) => r.scheduling)) return;
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [reels, load]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { review: 0, ready: 0, scheduled: 0, all: 0 };
    for (const r of reels ?? []) {
      c[reelStage(r).group] += 1;
      c.all += 1;
    }
    return c;
  }, [reels]);

  const shown = (reels ?? []).filter((r) => filter === "all" || reelStage(r).group === filter);
  const picked = (reels ?? []).filter((r) => selected.has(r.id));
  const toApprove = picked.filter((r) => r.approval_status === "PENDING" && r.qa_passed !== false);
  const toSchedule = picked.filter((r) => r.can_schedule && !r.scheduling);

  const toggle = (id: string, on: boolean) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  async function approveSelected() {
    setBulkBusy(true);
    setError(null);
    const failed: string[] = [];
    for (const r of toApprove) {
      try {
        await browserApiFetch(`/content/carousels/${r.id}/decision?brand=${encodeURIComponent(brand)}`, {
          method: "POST",
          body: JSON.stringify({ decision: "APPROVE" }),
        });
      } catch (err) {
        failed.push(`${r.title}: ${errorText(err)}`);
      }
    }
    setBulkBusy(false);
    if (failed.length) setError(failed.join(" · "));
    load();
  }

  if (activeBrand.data_origin !== "postgresql") {
    return (
      <EmptyState
        icon={<Clapperboard className="h-5 w-5" aria-hidden="true" />}
        title="Studio needs the live database"
        description="Uploading and scheduling your own reels and posts works once the dashboard is connected to Media OS."
        brandLabel={activeBrand.name}
      />
    );
  }

  return (
    <div className="space-y-6">
      <SurfaceCard
        icon={<Clapperboard />}
        title={`Studio — ${activeBrand.name}`}
        subtitle="Upload reels, photos and carousels you have made, write the caption, approve, then schedule them to Instagram and Facebook."
        badge={
          <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-slate-400 hover:bg-white/[0.05] hover:text-slate-100">
            <RefreshCw className="h-3 w-3" aria-hidden="true" /> Refresh
          </button>
        }
      >
        <UploadZone brand={brand} onUploaded={load} />
      </SurfaceCard>

      <AutopilotPanel brand={brand} />

      {error && <ErrorBanner title="Studio" message={error} />}

      {scheduling && toSchedule.length > 0 && (
        <BatchScheduler
          reels={toSchedule}
          brand={brand}
          onClose={() => {
            setScheduling(false);
            setSelected(new Set());
          }}
          onDone={load}
        />
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Post status">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              role="tab"
              aria-selected={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                filter === f.value
                  ? "border-sky-400/50 bg-sky-500/15 text-sky-200"
                  : "border-white/[0.1] bg-[var(--surface-inset)] text-slate-400 hover:text-slate-200"
              }`}
            >
              {f.label}
              <span className="ml-1.5 text-slate-500 tabular">{counts[f.value]}</span>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {shown.length > 0 && (
            <button
              type="button"
              onClick={() =>
                setSelected(shown.every((r) => selected.has(r.id)) ? new Set() : new Set(shown.map((r) => r.id)))
              }
              className="rounded-md px-2 py-1.5 text-slate-400 hover:bg-white/[0.05] hover:text-slate-100"
            >
              {shown.every((r) => selected.has(r.id)) ? "Clear selection" : "Select all"}
            </button>
          )}
          {toApprove.length > 0 && (
            <button
              type="button"
              onClick={() => void approveSelected()}
              disabled={bulkBusy}
              className="glass-chip btn-press inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium text-slate-100 disabled:opacity-60"
            >
              <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" /> Approve {toApprove.length}
            </button>
          )}
          {toSchedule.length > 0 && (
            <button
              type="button"
              onClick={() => setScheduling(true)}
              className="btn-press inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-b from-sky-300 to-sky-400 px-3 py-1.5 font-semibold text-[#04121a]"
            >
              <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" /> Schedule {toSchedule.length}
            </button>
          )}
        </div>
      </div>

      {reels === null && !error ? (
        <LoadingSkeleton rows={2} />
      ) : shown.length === 0 ? (
        <EmptyState
          icon={<Film className="h-5 w-5" aria-hidden="true" />}
          title={
            filter === "review"
              ? "Nothing waiting for review"
              : filter === "ready"
                ? "Nothing approved and waiting"
                : filter === "scheduled"
                  ? "Nothing scheduled yet"
                  : "Nothing uploaded yet"
          }
          description={
            <>
              Upload above. Approved posts also appear in{" "}
              <Link href={buildHrefWithBrand("/approvals")} className="text-sky-300 hover:underline">
                Approvals
              </Link>{" "}
              and on the calendar once scheduled.
            </>
          }
          brandLabel={activeBrand.name}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {shown.map((r) => (
            <ReelCard
              key={`${r.id}:${r.approval_status}:${r.caption ?? ""}:${r.title ?? ""}`}
              reel={r}
              brand={brand}
              selected={selected.has(r.id)}
              onSelect={(on) => toggle(r.id, on)}
              onChanged={load}
            />
          ))}
        </div>
      )}
    </div>
  );
}
