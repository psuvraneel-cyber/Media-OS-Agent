import React from "react";
import { AlertTriangle } from "lucide-react";

interface ErrorBannerProps {
  title?: string;
  message: string;
  code?: string;
  requestId?: string;
}

export function ErrorBanner({
  title = "Upstream Service Notice",
  message,
  code,
  requestId,
}: ErrorBannerProps) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" aria-hidden="true" />
        <div className="space-y-1">
          <p className="font-semibold text-rose-100">{title}</p>
          <p className="text-xs leading-relaxed text-rose-200/90">{message}</p>
          {(code || requestId) && (
            <p className="font-mono text-[11px] text-rose-300/80">
              {code ? `code=${code}` : ""}
              {code && requestId ? " | " : ""}
              {requestId ? `request_id=${requestId}` : ""}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading workspace state">
      {Array.from({ length: rows }).map((_, idx) => (
        <div
          key={idx}
          className="h-16 animate-pulse rounded-xl border border-slate-800 bg-slate-900/60"
        />
      ))}
    </div>
  );
}
