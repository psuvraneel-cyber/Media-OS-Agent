import React from "react";
import { AlertTriangle } from "lucide-react";

interface ErrorBannerProps {
  title?: string;
  message: string;
  code?: string;
  requestId?: string;
  action?: React.ReactNode;
}

export function ErrorBanner({
  title = "Upstream Service Notice",
  message,
  code,
  requestId,
  action,
}: ErrorBannerProps) {
  return (
    <div
      role="alert"
      className="animate-fade-in rounded-xl border border-rose-400/20 bg-rose-400/[0.06] p-4 text-sm text-rose-200"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" aria-hidden="true" />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="font-medium text-rose-100">{title}</p>
          <p className="text-xs leading-relaxed text-rose-200/80">{message}</p>
          {(code || requestId) && (
            <p className="font-mono text-[11px] text-rose-300/70">
              {code ? `code=${code}` : ""}
              {code && requestId ? " | " : ""}
              {requestId ? `request_id=${requestId}` : ""}
            </p>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
    </div>
  );
}

export function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading workspace state">
      {Array.from({ length: rows }).map((_, idx) => (
        <div key={idx} className="skeleton h-16 rounded-xl" />
      ))}
    </div>
  );
}

/** Inline-safe (renders a span) so it can sit inside headings and paragraphs while loading. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <span className={`skeleton block ${className}`} aria-hidden="true" />;
}
