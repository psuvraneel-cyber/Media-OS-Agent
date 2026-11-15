"use client";

import React, { useEffect, useId, useMemo, useRef, useState } from "react";

export interface ChartPoint {
  key: string;
  label: string;
  value: number | null;
}

interface AreaChartProps {
  points: ChartPoint[];
  height?: number;
  formatValue: (n: number) => string;
  ariaLabel: string;
  color?: string;
}

const PAD = { top: 12, right: 12, bottom: 26, left: 44 };
const DEFAULT_W = 720;

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / mag;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * mag;
}

/**
 * Dependency-free responsive SVG area chart. Draws in on mount, transitions on data change,
 * shows a keyboard- and pointer-accessible tooltip, and exposes the series as a hidden table for
 * screen readers.
 */
export function AreaChart({
  points,
  height = 220,
  formatValue,
  ariaLabel,
  color = "var(--chart-1)",
}: AreaChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const svgRef = useRef<SVGSVGElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  // Draw in real pixels so axis text stays 10px on phones instead of scaling down.
  const [W, setW] = useState(DEFAULT_W);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      if (w > 0) setW(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = height;
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const { path, area, xs, ys, max, ticks } = useMemo(() => {
    const vals = points.map((p) => p.value ?? 0);
    const m = niceMax(Math.max(0, ...vals));
    const n = points.length;
    const xPos = points.map((_, i) => PAD.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW));
    const yPos = vals.map((v) => PAD.top + innerH - (v / m) * innerH);
    const line = xPos.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${yPos[i].toFixed(1)}`);
    const base = PAD.top + innerH;
    const areaPath =
      n > 0
        ? `${line.join(" ")} L${xPos[n - 1].toFixed(1)},${base} L${xPos[0].toFixed(1)},${base} Z`
        : "";
    return {
      path: line.join(" "),
      area: areaPath,
      xs: xPos,
      ys: yPos,
      max: m,
      ticks: [0, 0.25, 0.5, 0.75, 1].map((f) => f * m),
    };
  }, [points, innerW, innerH]);

  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(W / 100))));

  function nearest(clientX: number): number | null {
    const svg = svgRef.current;
    if (!svg || xs.length === 0) return null;
    const rect = svg.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * W;
    let best = 0;
    for (let i = 1; i < xs.length; i++) {
      if (Math.abs(xs[i] - x) < Math.abs(xs[best] - x)) best = i;
    }
    return best;
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (points.length === 0) return;
    if (e.key === "ArrowRight") setHover((h) => Math.min(points.length - 1, (h ?? -1) + 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? points.length) - 1));
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  }

  const active = hover !== null ? points[hover] : null;

  return (
    <div className="relative" ref={wrapRef}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full touch-none select-none focus:outline-none"
        role="img"
        aria-label={ariaLabel}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onPointerMove={(e) => setHover(nearest(e.clientX))}
        onPointerLeave={() => setHover(null)}
        onBlur={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" style={{ stopColor: color, stopOpacity: 0.32 }} />
            <stop offset="55%" style={{ stopColor: "var(--chart-2)", stopOpacity: 0.08 }} />
            <stop offset="100%" style={{ stopColor: color, stopOpacity: 0 }} />
          </linearGradient>
          <linearGradient id={`${gradientId}-stroke`} x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" style={{ stopColor: "var(--chart-2)" }} />
            <stop offset="60%" style={{ stopColor: color }} />
            <stop offset="100%" style={{ stopColor: "var(--accent-strong)" }} />
          </linearGradient>
        </defs>
        {ticks.map((t) => {
          const y = PAD.top + innerH - (t / max) * innerH;
          return (
            <g key={t}>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={y}
                y2={y}
                stroke="var(--chart-grid)"
                strokeWidth="1"
              />
              <text
                x={PAD.left - 8}
                y={y + 3}
                textAnchor="end"
                fontSize="10"
                fill="var(--chart-axis)"
                className="tabular"
              >
                {formatValue(t)}
              </text>
            </g>
          );
        })}
        {points.map((p, i) =>
          i % labelEvery === 0 || i === points.length - 1 ? (
            <text
              key={p.key}
              x={xs[i]}
              y={H - 8}
              textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"}
              fontSize="10"
              fill="var(--chart-axis)"
            >
              {p.label}
            </text>
          ) : null,
        )}
        {points.length > 0 && (
          <>
            <path key={`a-${area.length}`} d={area} fill={`url(#${gradientId})`} className="chart-area" />
            <g className="chart-glow">
              <path
                key={`l-${points.length}-${max}`}
                d={path}
                fill="none"
                stroke={`url(#${gradientId}-stroke)`}
                strokeWidth="2.25"
                strokeLinejoin="round"
                strokeLinecap="round"
                className="chart-line"
                pathLength={2000}
                style={{ ["--path-length" as string]: 2000 }}
              />
            </g>
            {/* Live "latest value" marker */}
            {hover === null && (
              <g>
                <circle
                  cx={xs[xs.length - 1]}
                  cy={ys[ys.length - 1]}
                  r="3.5"
                  fill={color}
                  className="chart-pulse motion-reduce:hidden"
                />
                <circle
                  cx={xs[xs.length - 1]}
                  cy={ys[ys.length - 1]}
                  r="3.5"
                  fill="var(--accent-strong)"
                  stroke="var(--surface-panel)"
                  strokeWidth="2"
                />
              </g>
            )}
          </>
        )}
        {hover !== null && xs[hover] !== undefined && (
          <g>
            <line
              x1={xs[hover]}
              x2={xs[hover]}
              y1={PAD.top}
              y2={PAD.top + innerH}
              stroke={`url(#${gradientId}-stroke)`}
              strokeOpacity="0.55"
              strokeWidth="1"
            />
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={ys[hover]}
              y2={ys[hover]}
              stroke="rgba(255,255,255,0.08)"
              strokeDasharray="2 4"
            />
            <circle cx={xs[hover]} cy={ys[hover]} r="9" fill={color} opacity="0.15" />
            <circle cx={xs[hover]} cy={ys[hover]} r="4.5" fill="var(--surface-canvas)" stroke={color} strokeWidth="2.25" />
          </g>
        )}
      </svg>
      {active && hover !== null && (
        <div
          className="glass animate-scale-in pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-lg px-3 py-2 text-xs transition-[left] duration-150 ease-out"
          style={{ left: `${Math.min(88, Math.max(12, (xs[hover] / W) * 100))}%` }}
          role="status"
        >
          <p className="text-[11px] text-slate-400">{active.label}</p>
          <p className="font-semibold text-slate-100 tabular">
            {active.value === null ? "No data" : formatValue(active.value)}
          </p>
        </div>
      )}
      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <tbody>
          {points.map((p) => (
            <tr key={p.key}>
              <th scope="row">{p.label}</th>
              <td>{p.value === null ? "No data" : formatValue(p.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Horizontal bar list for categorical comparisons (platforms, accounts, tags). */
export function BarList({
  items,
  formatValue,
  emptyLabel = "No data for this period",
}: {
  items: Array<{ key: string; label: React.ReactNode; value: number | null; hint?: string; color?: string }>;
  formatValue: (n: number) => string;
  emptyLabel?: string;
}) {
  const max = Math.max(0, ...items.map((i) => i.value ?? 0));
  if (items.length === 0) {
    return <p className="py-4 text-center text-xs text-slate-500">{emptyLabel}</p>;
  }
  return (
    <ul className="space-y-2.5">
      {items.map((item) => {
        const pct = max > 0 ? ((item.value ?? 0) / max) * 100 : 0;
        return (
          <li key={item.key}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
              <span className="min-w-0 truncate text-slate-300">{item.label}</span>
              <span className="shrink-0 font-medium text-slate-100 tabular">
                {item.value === null ? "—" : formatValue(item.value)}
                {item.hint && <span className="ml-1.5 font-normal text-slate-500">{item.hint}</span>}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
              <div
                className="bar-fill h-full rounded-full"
                style={{
                  width: `${pct}%`,
                  background: `linear-gradient(90deg, color-mix(in srgb, ${item.color ?? "var(--chart-1)"} 55%, transparent), ${item.color ?? "var(--chart-1)"})`,
                  boxShadow: `0 0 10px color-mix(in srgb, ${item.color ?? "var(--chart-1)"} 45%, transparent)`,
                }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
