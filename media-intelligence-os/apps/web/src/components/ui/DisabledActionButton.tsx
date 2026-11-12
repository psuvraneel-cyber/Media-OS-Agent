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
      className={`inline-flex cursor-not-allowed items-center gap-2 rounded-lg border border-slate-700/70 bg-slate-800/60 px-3.5 py-2 text-xs font-medium text-slate-400 opacity-85 ${className}`}
    >
      <Lock className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
      <span>{label}</span>
      <span className="rounded bg-slate-900/90 px-1.5 py-0.5 text-[10px] tracking-wide text-slate-400 uppercase">
        {reason}
      </span>
    </button>
  );
}
