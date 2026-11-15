import React from "react";

interface SurfaceCardProps {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  badge?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /** Remove inner padding (for tables / charts that bleed to the edge). */
  flush?: boolean;
  as?: "section" | "div" | "article";
}

export function SurfaceCard({
  title,
  subtitle,
  badge,
  children,
  className = "",
  flush = false,
  as: Tag = "section",
}: SurfaceCardProps) {
  return (
    <Tag className={`surface animate-fade-in ${flush ? "" : "p-5"} ${className}`}>
      {(title || subtitle || badge) && (
        <header
          className={`flex flex-wrap items-start justify-between gap-3 ${flush ? "px-5 pt-5 pb-4" : "mb-4"}`}
        >
          <div className="min-w-0">
            {title && (
              <h2 className="text-sm font-semibold tracking-tight text-slate-100">{title}</h2>
            )}
            {subtitle && (
              <p className="mt-1 text-xs leading-relaxed text-slate-400">{subtitle}</p>
            )}
          </div>
          {badge && <div className="shrink-0">{badge}</div>}
        </header>
      )}
      {children}
    </Tag>
  );
}

/** Section heading used between groups of cards on a page. */
export function SectionHeader({
  title,
  description,
  actions,
  id,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  id?: string;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 id={id} className="text-[13px] font-semibold tracking-tight text-slate-200">
          {title}
        </h2>
        {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
