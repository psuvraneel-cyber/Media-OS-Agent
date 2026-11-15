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
      className={`animate-fade-in flex flex-col items-center justify-center rounded-xl border border-dashed border-white/[0.08] bg-white/[0.015] text-center ${compact ? "px-5 py-7" : "px-6 py-12"}`}
    >
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg border border-white/[0.08] bg-[var(--surface-elevated)] text-slate-300">
        {icon ?? <Info className="h-5 w-5" aria-hidden="true" />}
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
