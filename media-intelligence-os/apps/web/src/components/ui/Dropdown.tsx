"use client";

import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface DropdownOption<T extends string = string> {
  value: T;
  label: string;
  /** Second line under the label, e.g. "Unconfigured". */
  hint?: string;
  /** Leading visual: a logo, colour dot or icon. */
  icon?: React.ReactNode;
  disabled?: boolean;
}

/**
 * Themed single-select listbox (replaces the native <select>, whose OS popup ignores the
 * dark glass theme). Keyboard: ↑/↓, Home/End, Enter/Space, Esc, type-ahead.
 */
export function Dropdown<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  id,
  size = "md",
  variant = "field",
  align = "start",
  placeholder = "Select…",
  className = "",
  minWidth = "9rem",
}: {
  value: T;
  onChange: (value: T) => void;
  options: ReadonlyArray<DropdownOption<T>>;
  ariaLabel?: string;
  id?: string;
  size?: "sm" | "md";
  /** "field" = bordered input look; "ghost" = transparent (inside a chip). */
  variant?: "field" | "ghost";
  align?: "start" | "end";
  placeholder?: string;
  className?: string;
  minWidth?: string;
}) {
  const autoId = useId();
  const buttonId = id ?? `dd-${autoId}`;
  const listId = `${buttonId}-list`;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [dropUp, setDropUp] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const typed = useRef({ text: "", at: 0 });

  const selectedIndex = options.findIndex((o) => o.value === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined;

  const step = useCallback(
    (from: number, dir: 1 | -1) => {
      for (let i = 1; i <= options.length; i++) {
        const next = (from + dir * i + options.length) % options.length;
        if (!options[next]?.disabled) return next;
      }
      return from;
    },
    [options],
  );

  const openList = useCallback(() => {
    setActive(selectedIndex >= 0 ? selectedIndex : step(-1, 1));
    setOpen(true);
  }, [selectedIndex, step]);

  const close = useCallback((focusButton = true) => {
    setOpen(false);
    if (focusButton) buttonRef.current?.focus();
  }, []);

  const choose = useCallback(
    (i: number) => {
      const opt = options[i];
      if (!opt || opt.disabled) return;
      if (opt.value !== value) onChange(opt.value);
      close();
    },
    [options, value, onChange, close],
  );

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) close(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, close]);

  // Open upwards when there isn't room below; focus the list.
  useLayoutEffect(() => {
    if (!open) return;
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      const wanted = Math.min(320, options.length * 40 + 12);
      setDropUp(window.innerHeight - rect.bottom < wanted && rect.top > wanted);
    }
    listRef.current?.focus();
  }, [open, options.length]);

  // Keep the active option in view.
  useEffect(() => {
    if (!open) return;
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]);

  const onButtonKey = (e: React.KeyboardEvent) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      openList();
    }
  };

  const onListKey = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((a) => step(a, 1));
        return;
      case "ArrowUp":
        e.preventDefault();
        setActive((a) => step(a, -1));
        return;
      case "Home":
        e.preventDefault();
        setActive(step(-1, 1));
        return;
      case "End":
        e.preventDefault();
        setActive(step(0, -1));
        return;
      case "Enter":
      case " ":
        e.preventDefault();
        choose(active);
        return;
      case "Escape":
        e.preventDefault();
        close();
        return;
      case "Tab":
        close(false);
        return;
    }
    if (e.key.length === 1 && /\S/.test(e.key)) {
      const now = Date.now();
      const t = typed.current;
      t.text = now - t.at > 700 ? e.key.toLowerCase() : t.text + e.key.toLowerCase();
      t.at = now;
      const hit = options.findIndex((o) => !o.disabled && o.label.toLowerCase().startsWith(t.text));
      if (hit >= 0) setActive(hit);
    }
  };

  const h = size === "sm" ? "h-7 text-[11px]" : "h-8 text-xs";
  const trigger =
    variant === "field"
      ? `${h} rounded-lg border border-white/[0.08] bg-[var(--surface-inset)] px-2.5 hover:border-white/[0.16] hover:bg-white/[0.05] ${
          open ? "border-sky-400/60 bg-white/[0.05]" : ""
        }`
      : `${h} rounded-md px-1`;

  return (
    <div ref={rootRef} className={`relative inline-flex ${className}`}>
      <button
        ref={buttonRef}
        id={buttonId}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        onClick={() => (open ? close() : openList())}
        onKeyDown={onButtonKey}
        style={{ minWidth: variant === "field" ? minWidth : undefined }}
        className={`group inline-flex w-full items-center gap-2 text-left font-medium text-slate-100 transition-colors focus-visible:ring-2 focus-visible:ring-sky-400/50 focus-visible:outline-none ${trigger}`}
      >
        {selected?.icon && <span className="flex shrink-0 items-center">{selected.icon}</span>}
        <span className={`min-w-0 flex-1 truncate ${selected ? "" : "text-slate-500"}`}>
          {selected?.label ?? placeholder}
        </span>
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 text-slate-500 transition-transform duration-200 group-hover:text-slate-300 ${
            open ? "rotate-180 text-sky-300" : ""
          }`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          tabIndex={-1}
          aria-labelledby={ariaLabel ? undefined : buttonId}
          aria-label={ariaLabel}
          aria-activedescendant={`${buttonId}-opt-${active}`}
          onKeyDown={onListKey}
          className={`dropdown-panel absolute z-50 max-h-80 min-w-full overflow-y-auto p-1 focus:outline-none ${
            align === "end" ? "right-0" : "left-0"
          } ${dropUp ? "bottom-full mb-1.5 origin-bottom" : "top-full mt-1.5 origin-top"}`}
        >
          {options.map((opt, i) => {
            const isSelected = opt.value === value;
            const isActive = i === active;
            return (
              <li
                key={opt.value}
                id={`${buttonId}-opt-${i}`}
                data-index={i}
                role="option"
                aria-selected={isSelected}
                aria-disabled={opt.disabled || undefined}
                onMouseEnter={() => !opt.disabled && setActive(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(i)}
                className={`flex cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-xs whitespace-nowrap transition-colors ${
                  opt.disabled
                    ? "cursor-not-allowed text-slate-600"
                    : isActive
                      ? "bg-sky-400/[0.12] text-slate-50"
                      : "text-slate-300"
                }`}
              >
                {opt.icon && <span className="flex shrink-0 items-center">{opt.icon}</span>}
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{opt.label}</span>
                  {opt.hint && <span className="block truncate text-[10.5px] text-slate-500">{opt.hint}</span>}
                </span>
                <Check
                  className={`h-3.5 w-3.5 shrink-0 text-sky-300 ${isSelected ? "opacity-100" : "opacity-0"}`}
                  aria-hidden="true"
                />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
