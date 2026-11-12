import React from "react";
import { Info } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";

interface EmptyStateProps {
  title: string;
  description: string;
  phaseBadge: string;
  tone?: BadgeTone;
  brandLabel?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}

export function EmptyState({
  title,
  description,
  phaseBadge,
  tone = "disabled",
  brandLabel,
  icon,
  action,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-700/65 bg-[#0e131d] px-6 py-10 text-center">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-lg border border-slate-700/60 bg-slate-800/60 text-sky-300">
        {icon ?? <Info className="h-5 w-5" aria-hidden="true" />}
      </div>
      <div className="mb-2 flex flex-wrap items-center justify-center gap-2">
        <Badge tone={tone}>{phaseBadge}</Badge>
        {brandLabel && <Badge tone="info">{brandLabel}</Badge>}
      </div>
      <h3 className="text-base font-semibold text-slate-100">{title}</h3>
      <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-slate-400">{description}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
