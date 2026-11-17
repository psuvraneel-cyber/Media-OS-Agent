"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { ImagePlus, Loader2, Palette, Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Dropdown } from "@/components/ui/Dropdown";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { ApiClientError, browserApiFetch } from "@/lib/api-client";

export type Role = "COVER" | "CONTENT" | "CTA";
export const ROLES: ReadonlyArray<{ role: Role; label: string; hint: string }> = [
  { role: "COVER", label: "Cover", hint: "First slide, with the hook" },
  { role: "CONTENT", label: "Content", hint: "Used for every middle slide" },
  { role: "CTA", label: "Closing", hint: "Last slide, with the call to action" },
];

interface SlideCfg {
  asset_id: string | null;
  width: number | null;
  height: number | null;
  area: "top" | "middle" | "bottom";
  tone: "light" | "dark";
}

export interface DesignTemplate {
  id: string;
  name: string;
  is_active: boolean;
  auto_allowed: boolean;
  occasions: string[];
  slides: Record<Role, SlideCfg>;
  ready: boolean;
}

const OCCASIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "WEDDING_HINDU", label: "Hindu wedding" },
  { value: "WEDDING_MUSLIM", label: "Muslim wedding" },
  { value: "GAYE_HOLUD", label: "Gaye holud" },
  { value: "BOUBHAT_WALIMA", label: "Bou-bhat / walima" },
  { value: "ANNAPRASHAN_AQIQAH", label: "Annaprashan / aqiqah" },
  { value: "BIRTHDAY", label: "Birthday" },
  { value: "PUJA", label: "Puja" },
  { value: "UPANAYAN_GRIHAPRAVESH", label: "Upanayan / griha-pravesh" },
  { value: "GENERAL", label: "Everyday" },
];

const AREA_OPTIONS = [
  { value: "top", label: "Text at top" },
  { value: "middle", label: "Text in middle" },
  { value: "bottom", label: "Text at bottom" },
];
const TONE_OPTIONS = [
  { value: "light", label: "Light text" },
  { value: "dark", label: "Dark text" },
];

function errorText(err: unknown): string {
  return err instanceof ApiClientError ? err.message : "Request failed.";
}

/** Where the text panel sits on the thumbnail (same proportions as the real 1080×1350 slide). */
export const AREA_PREVIEW: Record<SlideCfg["area"], string> = {
  top: "top-[8%]",
  middle: "top-1/2 -translate-y-1/2",
  bottom: "bottom-[11%]",
};

function SlideSlot({
  brand,
  tpl,
  role,
  label,
  hint,
  onChanged,
}: {
  brand: string;
  tpl: DesignTemplate;
  role: Role;
  label: string;
  hint: string;
  onChanged: (t: DesignTemplate) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cfg = tpl.slides[role];
  const q = `?brand=${encodeURIComponent(brand)}`;

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      onChanged(
        await browserApiFetch<DesignTemplate>(`/content/templates/${tpl.id}/images/${role}${q}`, {
          method: "PUT",
          body: file,
          headers: { "Content-Type": file.type || "image/png" },
        }),
      );
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function patch(change: Partial<Pick<SlideCfg, "area" | "tone">>) {
    setError(null);
    try {
      onChanged(
        await browserApiFetch<DesignTemplate>(`/content/templates/${tpl.id}${q}`, {
          method: "PATCH",
          body: JSON.stringify({ slides: { [role]: change } }),
        }),
      );
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="min-w-0 space-y-2">
      <div>
        <p className="text-xs font-medium text-slate-200">{label}</p>
        <p className="text-[10.5px] text-slate-500">{hint}</p>
      </div>
      <button
        type="button"
        onClick={() => input.current?.click()}
        aria-label={`${cfg.asset_id ? "Replace" : "Upload"} ${label.toLowerCase()} background`}
        className="group relative block aspect-[4/5] w-full overflow-hidden rounded-lg border border-dashed border-white/[0.14] bg-[var(--surface-inset)] hover:border-sky-400/50"
      >
        {cfg.asset_id ? (
          // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
          <img
            src={`/api/v1/content/assets/${encodeURIComponent(cfg.asset_id)}/file${q}`}
            alt=""
            className="absolute inset-0 h-full w-full object-cover"
          />
        ) : (
          <span className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-[11px] text-slate-500">
            <ImagePlus className="h-5 w-5" aria-hidden="true" />
            1080 × 1350
          </span>
        )}
        {/* Where Media OS will place the text. */}
        <span
          aria-hidden="true"
          className={`absolute right-[6%] left-[6%] h-[30%] rounded-md border border-white/40 ${
            cfg.tone === "light" ? "bg-black/35" : "bg-white/60"
          } ${AREA_PREVIEW[cfg.area]}`}
        />
        {busy && (
          <span className="absolute inset-0 flex items-center justify-center bg-black/50">
            <Loader2 className="h-5 w-5 animate-spin text-white" aria-hidden="true" />
          </span>
        )}
      </button>
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        data-testid={`template-input-${role}`}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) void upload(f);
        }}
      />
      <Dropdown
        ariaLabel={`${label} text area`}
        size="sm"
        minWidth="100%"
        className="w-full"
        value={cfg.area}
        onChange={(v) => void patch({ area: v as SlideCfg["area"] })}
        options={AREA_OPTIONS}
      />
      <Dropdown
        ariaLabel={`${label} text colour`}
        size="sm"
        minWidth="100%"
        className="w-full"
        value={cfg.tone}
        onChange={(v) => void patch({ tone: v as SlideCfg["tone"] })}
        options={TONE_OPTIONS}
      />
      {error && <p role="alert" className="text-[11px] text-rose-300">{error}</p>}
    </div>
  );
}

function TemplateCard({
  brand,
  tpl,
  onChanged,
  onDeleted,
}: {
  brand: string;
  tpl: DesignTemplate;
  onChanged: (t: DesignTemplate) => void;
  onDeleted: () => void;
}) {
  const [name, setName] = useState(tpl.name);
  const [error, setError] = useState<string | null>(null);
  const q = `?brand=${encodeURIComponent(brand)}`;
  const missing = ROLES.filter((r) => !tpl.slides[r.role].asset_id).length;

  async function patch(change: Record<string, unknown>) {
    setError(null);
    try {
      onChanged(
        await browserApiFetch<DesignTemplate>(`/content/templates/${tpl.id}${q}`, {
          method: "PATCH",
          body: JSON.stringify(change),
        }),
      );
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function remove() {
    if (!window.confirm(`Delete "${tpl.name}"? Posts already made with it keep their design.`)) return;
    try {
      await browserApiFetch(`/content/templates/${tpl.id}${q}`, { method: "DELETE" });
      onDeleted();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const toggleOcc = (o: string) =>
    void patch({ occasions: tpl.occasions.includes(o) ? tpl.occasions.filter((x) => x !== o) : [...tpl.occasions, o] });

  return (
    <article className="rounded-xl border border-white/[0.07] bg-[var(--surface-inset)] p-4" aria-label={tpl.name}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && name !== tpl.name && void patch({ name })}
          aria-label="Template name"
          className="min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-1 text-sm font-semibold text-slate-100 hover:border-white/[0.1] focus:border-sky-400/60 focus:outline-none"
        />
        <Badge tone={tpl.ready ? (tpl.is_active ? "live" : "disabled") : "prototype"}>
          {tpl.ready ? (tpl.is_active ? "Ready" : "Switched off") : `Needs ${missing} image${missing === 1 ? "" : "s"}`}
        </Badge>
        <button
          type="button"
          onClick={() => void remove()}
          aria-label={`Delete ${tpl.name}`}
          className="rounded-md p-1.5 text-slate-500 hover:bg-white/[0.05] hover:text-rose-300"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {ROLES.map((r) => (
          <SlideSlot key={r.role} brand={brand} tpl={tpl} role={r.role} label={r.label} hint={r.hint} onChanged={onChanged} />
        ))}
      </div>

      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-200">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" className="h-3.5 w-3.5 accent-sky-400" checked={tpl.is_active} onChange={(e) => void patch({ is_active: e.target.checked })} />
          On
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            className="h-3.5 w-3.5 accent-sky-400"
            checked={tpl.auto_allowed}
            onChange={(e) => void patch({ auto_allowed: e.target.checked })}
          />
          Media OS may choose it automatically
        </label>
      </div>
      <div className="mt-3">
        <p className="mb-1.5 text-[11px] text-slate-500">
          Best for (optional; when Media OS chooses, it prefers sets that match the post&apos;s occasion)
        </p>
        <div className="flex flex-wrap gap-1.5">
          {OCCASIONS.map((o) => {
            const on = tpl.occasions.includes(o.value);
            return (
              <button
                key={o.value}
                type="button"
                aria-pressed={on}
                onClick={() => toggleOcc(o.value)}
                className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
                  on ? "border-sky-400/50 bg-sky-500/15 text-sky-200" : "border-white/[0.1] text-slate-400 hover:text-slate-200"
                }`}
              >
                {o.label}
              </button>
            );
          })}
        </div>
      </div>
      {error && <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
    </article>
  );
}

/** Assets page: the owner's carousel design template sets. */
export function DesignTemplates({ brand }: { brand: string }) {
  const [items, setItems] = useState<DesignTemplate[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = `?brand=${encodeURIComponent(brand)}`;

  const load = useCallback(() => {
    browserApiFetch<{ items: DesignTemplate[] }>(`/content/templates${q}`)
      .then((r) => setItems(r.items))
      .catch((err: unknown) => setError(errorText(err)));
  }, [q]);

  useEffect(() => {
    load();
  }, [load]);

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const t = await browserApiFetch<DesignTemplate>(`/content/templates${q}`, {
        method: "POST",
        body: JSON.stringify({ name: name.trim() }),
      });
      setItems((cur) => [...(cur ?? []), t]);
      setName("");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const replace = (t: DesignTemplate) => setItems((cur) => (cur ?? []).map((x) => (x.id === t.id ? t : x)));

  return (
    <SurfaceCard
      icon={<Palette />}
      title="Design templates"
      subtitle="Your own carousel backgrounds. Design them anywhere (e.g. Canva) at 1080 × 1350, leaving clean space where the text goes. Media OS places the text and keeps it readable."
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void create()}
          placeholder="New template set, e.g. Gold mandala"
          aria-label="New template set name"
          className="h-8 min-w-[14rem] flex-1 rounded-lg border border-white/[0.08] bg-[var(--surface-inset)] px-2.5 text-xs text-slate-100 focus:border-sky-400/60 focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void create()}
          disabled={busy || !name.trim()}
          className="btn-press inline-flex h-8 items-center gap-1.5 rounded-lg bg-gradient-to-b from-sky-300 to-sky-400 px-3 text-xs font-semibold text-[#04121a] disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Plus className="h-3.5 w-3.5" aria-hidden="true" />}
          Add set
        </button>
      </div>
      {error && <p role="alert" className="mb-3 text-xs text-rose-300">{error}</p>}
      {items && items.length === 0 && (
        <p className="rounded-lg border border-dashed border-white/[0.08] px-4 py-5 text-center text-xs text-slate-400">
          No template sets yet. Posts use the built-in Nimontron design until you add one.
        </p>
      )}
      <div className="space-y-4">
        {(items ?? []).map((t) => (
          <TemplateCard key={t.id} brand={brand} tpl={t} onChanged={replace} onDeleted={load} />
        ))}
      </div>
    </SurfaceCard>
  );
}
