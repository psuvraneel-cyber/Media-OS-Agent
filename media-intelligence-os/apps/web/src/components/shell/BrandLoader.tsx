import React from "react";

/**
 * The Media OS mark in motion: the app icon inside a rotating gold/blue orbit ring with tick
 * marks, an orbiting satellite, twinkling sparkles and a breathing halo. Same design as the
 * desktop splash screen. Used by the route loading screen.
 */
export function BrandLoader({ label = "Loading", detail }: { label?: string; detail?: string }) {
  return (
    <div role="status" aria-live="polite" className="brand-loader">
      <div className="brand-loader__mark" aria-hidden="true">
        <span className="brand-loader__halo" />
        <span className="brand-loader__ticks" />
        <span className="brand-loader__ring" />
        <span className="brand-loader__ring brand-loader__ring--inner" />
        <span className="brand-loader__orbit" />
        {/* eslint-disable-next-line @next/next/no-img-element -- tiny static brand asset */}
        <img className="brand-loader__icon" src="/brand/mark.png" alt="" width={84} height={84} />
        <i className="brand-loader__spark brand-loader__spark--n" />
        <i className="brand-loader__spark brand-loader__spark--e" />
        <i className="brand-loader__spark brand-loader__spark--s" />
        <i className="brand-loader__spark brand-loader__spark--w" />
      </div>
      <p className="brand-loader__label font-display">{label}</p>
      {detail && <p className="brand-loader__detail">{detail}</p>}
      <span className="brand-loader__bar" aria-hidden="true" />
    </div>
  );
}
