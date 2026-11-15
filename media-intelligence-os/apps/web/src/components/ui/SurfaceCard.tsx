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
  /** Feature panel: accent hairline and soft glow from the top edge. */
  hero?: boolean;
  /** Small glowing icon tile beside the title. */
  icon?: React.ReactNode;
}

export function SurfaceCard({
  title,
  subtitle,
  badge,
  children,
  className = "",
  flush = false,
  as: Tag = "section",
  hero = false,
  icon,
}: SurfaceCardProps) {
  return (
    <Tag className={`surface animate-fade-in ${hero ? "surface-hero" : ""} ${flush ? "" : "p-5"} ${className}`}>
      {(title || subtitle || badge) && (
        <header
          className={`flex flex-wrap items-start justify-between gap-3 ${flush ? "px-5 pt-5 pb-4" : "mb-4"}`}
        >
          <div className="flex min-w-0 items-start gap-3">
            {icon && (
              <span
                aria-hidden="true"
                className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.12] bg-gradient-to-br from-sky-300/20 to-indigo-400/15 text-sky-200 shadow-[0_8px_20px_-10px_rgba(34,211,238,0.7),inset_0_1px_0_rgba(255,255,255,0.16)] [&>svg]:h-4 [&>svg]:w-4"
              >
                {icon}
              </span>
            )}
            <div className="min-w-0">
              {title && (
                <h2 className="text-base font-semibold tracking-tight text-slate-50">{title}</h2>
              )}
              {subtitle && (
                <p className="mt-1 text-xs leading-relaxed text-slate-400">{subtitle}</p>
              )}
            </div>
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
        <h2 id={id} className="flex items-center gap-2 text-sm font-semibold tracking-tight text-slate-100">
          <span aria-hidden="true" className="h-3.5 w-[3px] rounded-full bg-gradient-to-b from-sky-300 to-sky-600" />
          {title}
        </h2>
        {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
