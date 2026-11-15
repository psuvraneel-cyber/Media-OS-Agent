"use client";

import React, { useEffect, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";

/**
 * Glowing top-of-page progress bar for in-app navigation. Starts when an internal link is
 * clicked, creeps toward 85% while the server renders, and completes when the route changes.
 * Writes straight to the element's style, so it never re-renders the shell.
 */
export function RouteProgress() {
  const barRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const timers = useRef<{ creep?: ReturnType<typeof setInterval>; hide?: ReturnType<typeof setTimeout> }>({});
  const active = useRef(false);

  useEffect(() => {
    const bar = barRef.current;
    if (!bar) return;
    const set = (p: number, visible = true) => {
      bar.style.setProperty("--progress", String(p));
      bar.style.opacity = visible ? "1" : "0";
    };

    const start = () => {
      clearTimeout(timers.current.hide);
      clearInterval(timers.current.creep);
      active.current = true;
      let p = 0.12;
      set(p);
      timers.current.creep = setInterval(() => {
        p += (0.85 - p) * 0.12;
        set(p);
      }, 200);
    };

    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target instanceof Element ? e.target.closest("a") : null;
      if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      start();
    };

    document.addEventListener("click", onClick, true);
    const t = timers.current;
    return () => {
      document.removeEventListener("click", onClick, true);
      clearInterval(t.creep);
      clearTimeout(t.hide);
    };
  }, []);

  // Route committed: finish the bar.
  useEffect(() => {
    const bar = barRef.current;
    if (!bar || !active.current) return;
    active.current = false;
    clearInterval(timers.current.creep);
    bar.style.setProperty("--progress", "1");
    timers.current.hide = setTimeout(() => {
      bar.style.opacity = "0";
      timers.current.hide = setTimeout(() => bar.style.setProperty("--progress", "0"), 450);
    }, 250);
  }, [pathname, searchParams]);

  return <div ref={barRef} className="route-progress" style={{ opacity: 0 }} aria-hidden="true" />;
}

/** Sets data-scrolled on the given element once the page scrolls, for the header's depth state. */
export function useScrolledAttribute(ref: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      el.dataset.scrolled = window.scrollY > 8 ? "true" : "false";
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [ref]);
}
