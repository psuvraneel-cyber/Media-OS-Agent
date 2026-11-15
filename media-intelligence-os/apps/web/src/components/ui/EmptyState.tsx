import React from "react";
import { Info } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";

interface EmptyStateProps {
  title: string;
  description: React.ReactNode;
  /** Optional status label (e.g. the delivery phase). Kept small so it never dominates. */
  phaseBadge?: string;
  tone?: BadgeTone;
  brandLabel?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
}

export function EmptyState({
  title,
  description,
  phaseBadge,
  tone = "disabled",
  brandLabel,
  icon,
  action,
  compact = false,
}: EmptyStateProps) {
  return (
    <div
      className={`animate-fade-in relative flex flex-col items-center justify-center overflow-hidden rounded-xl border border-dashed border-white/[0.08] bg-[radial-gradient(28rem_12rem_at_50%_0%,rgba(34,211,238,0.06),transparent_70%)] text-center ${compact ? "px-5 py-7" : "px-6 py-14"}`}
    >
      {/* Concentric rings behind the icon: a quiet "nothing here yet" motif. */}
      <div className="relative mb-4 flex items-center justify-center">
        {!compact && (
          <>
            <span aria-hidden="true" className="absolute h-24 w-24 rounded-full border border-white/[0.04]" />
            <span aria-hidden="true" className="absolute h-16 w-16 rounded-full border border-white/[0.06]" />
          </>
        )}
        <div className="relative flex h-11 w-11 items-center justify-center rounded-xl border border-white/[0.1] bg-gradient-to-b from-[var(--surface-elevated)] to-[var(--surface-panel)] text-sky-300 shadow-[0_8px_24px_-10px_rgba(34,211,238,0.45),inset_0_1px_0_rgba(255,255,255,0.06)]">
          {icon ?? <Info className="h-5 w-5" aria-hidden="true" />}
        </div>
      </div>
      <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
      <div className="mt-1.5 max-w-lg text-[13px] leading-relaxed text-slate-400">
        {description}
      </div>
      {(phaseBadge || brandLabel) && (
        <div className="mt-3 flex flex-wrap items-center justify-center gap-1.5">
          {phaseBadge && <Badge tone={tone}>{phaseBadge}</Badge>}
          {brandLabel && <Badge tone="neutral">{brandLabel}</Badge>}
        </div>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
