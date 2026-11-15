"use client";

import React, { useEffect, useState } from "react";
import { Copy, Droplets, Minus, Sparkles, Square, X } from "lucide-react";

/*
 * Desktop-shell controls in the header (apps/desktop only; renders nothing in a browser):
 *  - a Clear / Acrylic toggle for the see-through window look (also Ctrl+Shift+L), and
 *  - minimise / maximise / close buttons in the "clear" look, where the window is frameless and
 *    Windows can't draw its own caption buttons.
 */

type Look = "clear" | "acrylic";

interface DesktopBridge {
  getLook?: () => Promise<{ look: Look; maximized: boolean } | null>;
  setLook?: (look: Look) => Promise<boolean>;
  minimize?: () => Promise<boolean>;
  toggleMaximize?: () => Promise<boolean>;
  close?: () => Promise<boolean>;
  onMaximized?: (cb: (maximized: boolean) => void) => () => void;
}

function getBridge(): DesktopBridge | undefined {
  return typeof window === "undefined"
    ? undefined
    : (window as unknown as { mediaOsDesktop?: DesktopBridge }).mediaOsDesktop;
}

const LOOKS: Array<{ value: Look; label: string; hint: string; Icon: typeof Droplets }> = [
  { value: "clear", label: "Clear", hint: "See-through, no blur", Icon: Droplets },
  { value: "acrylic", label: "Acrylic", hint: "See-through with Windows blur", Icon: Sparkles },
];

export function DesktopChrome() {
  const [state, setState] = useState<{ look: Look; maximized: boolean } | null>(null);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    const bridge = getBridge();
    if (!bridge?.getLook) return;
    let disposed = false;
    void bridge.getLook().then((s) => {
      if (!disposed && s) setState(s);
    });
    const off = bridge.onMaximized?.((maximized) => setState((s) => (s ? { ...s, maximized } : s)));
    return () => {
      disposed = true;
      off?.();
    };
  }, []);

  if (!state) return null;
  const bridge = getBridge();

  const choose = (look: Look) => {
    if (look === state.look || switching) return;
    setSwitching(true);
    void bridge?.setLook?.(look);
  };

  return (
    <div className="flex items-center gap-2">
      <div
        role="radiogroup"
        aria-label="Window look"
        title="Window look (Ctrl+Shift+L)"
        className="glass-chip hidden items-center rounded-lg p-0.5 md:inline-flex"
      >
        {LOOKS.map(({ value, label, hint, Icon }) => {
          const active = state.look === value;
          return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={active}
              title={hint}
              disabled={switching}
              onClick={() => choose(value)}
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${
                active
                  ? "bg-white/[0.12] text-slate-50 shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]"
                  : "text-slate-400 hover:text-slate-100"
              }`}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>

      {state.look === "clear" && (
        <div className="-mr-2 flex items-center sm:-mr-4 lg:-mr-6">
          <button
            type="button"
            aria-label="Minimise window"
            onClick={() => void bridge?.minimize?.()}
            className="flex h-9 w-11 items-center justify-center rounded-md text-slate-300 transition-colors hover:bg-white/[0.08] hover:text-white"
          >
            <Minus className="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label={state.maximized ? "Restore window" : "Maximise window"}
            onClick={() => void bridge?.toggleMaximize?.()}
            className="flex h-9 w-11 items-center justify-center rounded-md text-slate-300 transition-colors hover:bg-white/[0.08] hover:text-white"
          >
            {state.maximized ? (
              <Copy className="h-3.5 w-3.5 -scale-x-100" aria-hidden="true" />
            ) : (
              <Square className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            aria-label="Close window"
            onClick={() => void bridge?.close?.()}
            className="flex h-9 w-11 items-center justify-center rounded-md text-slate-300 transition-colors hover:bg-[#c42b1c] hover:text-white"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
}
