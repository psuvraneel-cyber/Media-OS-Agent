import React from "react";
import { Lock } from "lucide-react";

interface DisabledActionButtonProps {
  label: string;
  reason: string;
  className?: string;
}

export function DisabledActionButton({
  label,
  reason,
  className = "",
}: DisabledActionButtonProps) {
  return (
    <button
      type="button"
      disabled
      aria-disabled="true"
      title={reason}
      className={`inline-flex cursor-not-allowed items-center gap-2 rounded-lg border border-dashed border-white/[0.1] bg-white/[0.02] px-3.5 py-2 text-xs font-medium text-slate-400 ${className}`}
    >
      <Lock className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
      <span>{label}</span>
      <span className="rounded border border-white/[0.06] bg-[var(--surface-inset)] px-1.5 py-0.5 text-[10px] tracking-wide text-slate-500 uppercase">
        {reason}
      </span>
    </button>
  );
}
