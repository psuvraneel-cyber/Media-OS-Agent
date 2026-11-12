import React from "react";

export type BadgeTone = "live" | "prototype" | "disabled" | "blocked" | "info";

const TONE_CLASSES: Record<BadgeTone, string> = {
  live: "border-emerald-500/35 bg-emerald-500/12 text-emerald-300",
  prototype: "border-amber-500/35 bg-amber-500/12 text-amber-300",
  disabled: "border-slate-500/35 bg-slate-500/12 text-slate-300",
  blocked: "border-rose-500/40 bg-rose-500/14 text-rose-300",
  info: "border-sky-400/35 bg-sky-400/12 text-sky-300",
};

interface BadgeProps {
  tone?: BadgeTone;
  children: React.ReactNode;
  className?: string;
}

export function Badge({ tone = "info", children, className = "" }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium tracking-wide uppercase ${TONE_CLASSES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
