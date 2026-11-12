import React from "react";

interface SurfaceCardProps {
  title?: string;
  subtitle?: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export function SurfaceCard({
  title,
  subtitle,
  badge,
  children,
  className = "",
}: SurfaceCardProps) {
  return (
    <section
      className={`rounded-xl border border-slate-700/45 bg-[#121824] p-5 shadow-sm ${className}`}
    >
      {(title || subtitle || badge) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-slate-700/40 pb-3.5">
          <div>
            {title && (
              <h2 className="text-sm font-semibold tracking-tight text-slate-100">{title}</h2>
            )}
            {subtitle && <p className="mt-1 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {badge && <div className="shrink-0">{badge}</div>}
        </header>
      )}
      {children}
    </section>
  );
}
