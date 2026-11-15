import React from "react";

export type BadgeTone = "live" | "prototype" | "disabled" | "blocked" | "info" | "neutral";

const TONE_CLASSES: Record<BadgeTone, string> = {
  live: "border-emerald-400/20 bg-emerald-400/10 text-emerald-300",
  prototype: "border-amber-400/20 bg-amber-400/10 text-amber-300",
  disabled: "border-white/[0.08] bg-white/[0.04] text-slate-400",
  blocked: "border-rose-400/25 bg-rose-400/10 text-rose-300",
  info: "border-sky-400/20 bg-sky-400/10 text-sky-300",
  neutral: "border-white/[0.08] bg-white/[0.03] text-slate-300",
};

const DOT_CLASSES: Record<BadgeTone, string> = {
  live: "bg-emerald-400",
  prototype: "bg-amber-400",
  disabled: "bg-slate-500",
  blocked: "bg-rose-400",
  info: "bg-sky-400",
  neutral: "bg-slate-400",
};

interface BadgeProps {
  tone?: BadgeTone;
  children: React.ReactNode;
  className?: string;
  dot?: boolean;
}

export function Badge({ tone = "info", children, className = "", dot = false }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11px] leading-4 font-medium whitespace-nowrap ${TONE_CLASSES[tone]} ${className}`}
    >
      {dot && (
        <span className={`h-1.5 w-1.5 rounded-full ${DOT_CLASSES[tone]} ${GLOW_CLASSES[tone] ?? ""}`} aria-hidden="true" />
      )}
      {children}
    </span>
  );
}

const GLOW_CLASSES: Partial<Record<BadgeTone, string>> = {
  live: "shadow-[0_0_8px_rgba(45,212,191,0.65)]",
  blocked: "shadow-[0_0_8px_rgba(251,113,133,0.6)]",
  prototype: "shadow-[0_0_8px_rgba(251,191,36,0.5)]",
  info: "shadow-[0_0_8px_rgba(34,211,238,0.6)]",
};

/**
 * `pulse` = the primary live signal (sonar ping). Other healthy dots get a calm breathing halo so
 * the screen feels alive without competing pings everywhere.
 */
export function StatusDot({ tone, pulse = false }: { tone: BadgeTone; pulse?: boolean }) {
  const breathe = !pulse && tone === "live";
  return (
    <span className="relative inline-flex h-2 w-2" aria-hidden="true">
      {pulse && (
        <span
          className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-40 motion-reduce:hidden ${DOT_CLASSES[tone]}`}
        />
      )}
      {breathe && (
        <span
          className={`animate-glow-breathe absolute inline-flex h-full w-full rounded-full motion-reduce:hidden ${DOT_CLASSES[tone]}`}
        />
      )}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${DOT_CLASSES[tone]} ${GLOW_CLASSES[tone] ?? ""}`} />
    </span>
  );
}
