"use client";

import React, { useEffect } from "react";

/** Fixed, decorative backdrop: drifting aurora fields over a fading engineering grid. */
export function AmbientBackground() {
  return (
    <div className="ambient" aria-hidden="true">
      <div className="ambient-grid" />
      <div className="ambient-blob ambient-blob--a" />
      <div className="ambient-blob ambient-blob--b" />
      <div className="ambient-blob ambient-blob--c" />
      <div className="ambient-blob ambient-blob--d" />
      <div className="ambient-blob ambient-blob--e" />
    </div>
  );
}

/**
 * One document-level pointer listener that feeds the hovered `.surface` its cursor position as
 * `--mx` / `--my`, driving the spotlight wash and border arc in globals.css. rAF-throttled, and
 * it only writes to the single panel under the pointer.
 */
export function useSurfaceSpotlight() {
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia?.("(pointer: fine)").matches) return;
    let frame = 0;
    let lastEvent: PointerEvent | null = null;
    let tilted: HTMLElement | null = null;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const resetTilt = () => {
      if (!tilted) return;
      tilted.style.removeProperty("--rx");
      tilted.style.removeProperty("--ry");
      tilted = null;
    };

    const apply = () => {
      frame = 0;
      const e = lastEvent;
      if (!e) return;
      const el = e.target instanceof Element ? e.target : null;

      const target = el?.closest<HTMLElement>(".surface") ?? null;
      if (target) {
        const rect = target.getBoundingClientRect();
        target.style.setProperty("--mx", `${e.clientX - rect.left}px`);
        target.style.setProperty("--my", `${e.clientY - rect.top}px`);
      }

      // 3D tilt toward the cursor for `.tilt` tiles (max angle from --tilt-max, default 6deg).
      const tile = reduceMotion ? null : (el?.closest<HTMLElement>(".tilt") ?? null);
      if (tile !== tilted) resetTilt();
      if (tile) {
        const r = tile.getBoundingClientRect();
        const max = parseFloat(getComputedStyle(tile).getPropertyValue("--tilt-max")) || 6;
        const px = (e.clientX - r.left) / r.width - 0.5;
        const py = (e.clientY - r.top) / r.height - 0.5;
        tile.style.setProperty("--ry", `${(px * max * 2).toFixed(2)}deg`);
        tile.style.setProperty("--rx", `${(-py * max * 2).toFixed(2)}deg`);
        tilted = tile;
      }
    };

    const onMove = (e: PointerEvent) => {
      lastEvent = e;
      if (!frame) frame = requestAnimationFrame(apply);
    };

    const onLeave = () => resetTilt();
    document.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      resetTilt();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
}
