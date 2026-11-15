"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Info, Minus } from "lucide-react";

/* ------------------------------------------------------------------ animated number */

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Counts from the previous value to the new one (~450ms, ease-out). Static under reduced motion. */
export function AnimatedNumber({
  value,
  format,
  className = "",
}: {
  value: number;
  format: (n: number) => string;
  className?: string;
}) {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);

  useEffect(() => {
    const from = fromRef.current;
    fromRef.current = value;
    if (from === value || prefersReducedMotion() || typeof requestAnimationFrame !== "function") {
      setDisplay(value);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const duration = 450;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(from + (value - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);

  return (
    <span className={`tabular ${className}`} aria-live="polite">
      {format(display)}
    </span>
  );
}

/* ------------------------------------------------------------------ segmented control */

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
}

/** Radio-group segmented control with a sliding indicator. Arrow keys move the selection. */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
}: {
  options: ReadonlyArray<SegmentOption<T>>;
  value: T;
  onChange: (value: T) => void;
  label: string;
  size?: "sm" | "md";
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);
  const activeIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );

  useEffect(() => {
    const el = refs.current[activeIndex];
    if (el) setIndicator({ left: el.offsetLeft, width: el.offsetWidth });
  }, [activeIndex, options.length]);

  function onKeyDown(event: React.KeyboardEvent) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let next = activeIndex;
    if (event.key === "ArrowRight") next = (activeIndex + 1) % options.length;
    if (event.key === "ArrowLeft") next = (activeIndex - 1 + options.length) % options.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = options.length - 1;
    onChange(options[next].value);
    refs.current[next]?.focus();
  }

  const pad = size === "sm" ? "px-2.5 py-1 text-[11px]" : "px-3 py-1.5 text-xs";
  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      className="relative inline-flex items-center rounded-lg border border-white/[0.07] bg-[var(--surface-inset)] p-0.5"
    >
      {indicator && (
        <span
          aria-hidden="true"
          className="absolute top-0.5 bottom-0.5 rounded-md border border-white/[0.08] bg-[var(--surface-elevated)] shadow-sm transition-all duration-200 ease-out"
          style={{ left: indicator.left, width: indicator.width }}
        />
      )}
      {options.map((opt, i) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(opt.value)}
            className={`relative z-10 rounded-md font-medium whitespace-nowrap transition-colors duration-150 ${pad} ${
              selected ? "text-slate-50" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ select */

export function Select({
  label,
  value,
  onChange,
  options,
  id,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: ReadonlyArray<{ value: string; label: string }>;
  id?: string;
}) {
  const autoId = useId();
  const selectId = id ?? autoId;
  return (
    <label htmlFor={selectId} className="flex flex-col gap-1">
      <span className="text-[11px] font-medium text-slate-500">{label}</span>
      <select
        id={selectId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 min-w-[9rem] rounded-lg border border-white/[0.08] bg-[var(--surface-inset)] px-2.5 text-xs text-slate-200 transition-colors hover:border-white/[0.14] focus:border-sky-400/60 focus:outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/* ------------------------------------------------------------------ info tooltip */

/** Accessible tooltip: shows on hover and keyboard focus; text is also the button's description. */
export function InfoTip({ text, label = "More information" }: { text: string; label?: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        className="rounded text-slate-500 transition-colors hover:text-slate-300"
      >
        <Info className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      <span
        id={id}
        role="tooltip"
        className={
          open
            ? "animate-fade-in pointer-events-none absolute top-full left-1/2 z-40 mt-2 w-64 max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-lg border border-white/[0.1] bg-[var(--surface-elevated)] px-3 py-2 text-left text-[11px] leading-relaxed font-normal text-slate-300 shadow-[var(--shadow-lg)]"
            : "sr-only"
        }
      >
        {text}
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------ delta pill */

export function DeltaPill({ pct, suffix = "vs previous period" }: { pct: number | null; suffix?: string }) {
  if (pct === null || Number.isNaN(pct)) {
    return <span className="text-xs text-slate-500">No comparable previous period</span>;
  }
  const up = pct > 0;
  const flat = pct === 0;
  const Icon = flat ? Minus : up ? ArrowUpRight : ArrowDownRight;
  const tone = flat ? "text-slate-400" : up ? "text-emerald-300" : "text-rose-300";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span className={`inline-flex items-center gap-0.5 font-medium tabular ${tone}`}>
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        {up ? "+" : ""}
        {pct.toFixed(1)}%
      </span>
      <span className="text-slate-500">{suffix}</span>
    </span>
  );
}
