"use client";

import React, { useEffect } from "react";

interface ConfirmActionDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmActionDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  pending = false,
  onConfirm,
  onCancel,
}: ConfirmActionDialogProps) {
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) {
        onCancel();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, pending, onCancel]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-desc"
    >
      <div className="glass animate-scale-in w-full max-w-md rounded-xl p-5">
        <h3 id="confirm-dialog-title" className="text-base font-semibold text-slate-100">
          {title}
        </h3>
        <p id="confirm-dialog-desc" className="mt-2 text-sm leading-relaxed text-slate-400">
          {description}
        </p>
        <div className="mt-5 flex justify-end gap-2.5">
          <button
            type="button"
            disabled={pending}
            onClick={onCancel}
            className="rounded-lg border border-white/[0.1] bg-[var(--surface-elevated)] px-3.5 py-2 text-xs font-medium text-slate-200 hover:border-white/[0.18] hover:bg-white/[0.08] disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={onConfirm}
            className="rounded-lg border border-sky-400/40 bg-sky-500/20 px-3.5 py-2 text-xs font-semibold text-sky-200 hover:bg-sky-500/30 disabled:opacity-50"
          >
            {pending ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
