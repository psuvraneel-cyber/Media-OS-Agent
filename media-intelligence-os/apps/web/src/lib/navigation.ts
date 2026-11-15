export type NavGroupId = "intelligence" | "production" | "performance" | "system";

export interface NavRouteItem {
  id: string;
  href: string;
  label: string;
  shortDesc: string;
  group: NavGroupId;
  targetPhase: string;
  iconName:
    | "layout-dashboard"
    | "radar"
    | "list-filter"
    | "clapperboard"
    | "folder-kanban"
    | "calendar-clock"
    | "shield-check"
    | "line-chart"
    | "bot"
    | "plug"
    | "sliders-horizontal"
    | "terminal";
}

// Grouped by what the owner is doing, not by delivery phase. Phase stays in route metadata and
// appears only as a quiet label in the page header.
export const NAV_GROUPS: ReadonlyArray<{ id: NavGroupId; label: string }> = [
  { id: "intelligence", label: "Intelligence" },
  { id: "production", label: "Content production" },
  { id: "performance", label: "Performance" },
  { id: "system", label: "System" },
];

export const NAV_ROUTES: ReadonlyArray<NavRouteItem> = [
  {
    id: "dashboard",
    href: "/dashboard",
    label: "Overview",
    shortDesc: "Health, audience attention and pipeline status for the selected brand",
    group: "intelligence",
    targetPhase: "Live",
    iconName: "layout-dashboard",
  },
  {
    id: "research",
    href: "/research",
    label: "Research",
    shortDesc: "Approved source registry & topic discovery queue",
    group: "intelligence",
    targetPhase: "Phase 5 · sources off",
    iconName: "radar",
  },
  {
    id: "topics",
    href: "/topics",
    label: "Topics",
    shortDesc: "Editorial backlog, deduplication & story scoring rubric",
    group: "intelligence",
    targetPhase: "Phase 5–6",
    iconName: "list-filter",
  },
  {
    id: "studio",
    href: "/studio",
    label: "Studio",
    shortDesc: "Script drafting, carousel layouts & narrated vertical video builder",
    group: "production",
    targetPhase: "N2 · CLI generation",
    iconName: "clapperboard",
  },
  {
    id: "assets",
    href: "/assets",
    label: "Assets",
    shortDesc: "Brand-isolated media assets, renders & audio artifacts",
    group: "production",
    targetPhase: "Phase 7",
    iconName: "folder-kanban",
  },
  {
    id: "calendar",
    href: "/calendar",
    label: "Calendar",
    shortDesc: "Scheduled and published posts by day, with overdue warnings",
    group: "production",
    targetPhase: "N3 live",
    iconName: "calendar-clock",
  },
  {
    id: "approvals",
    href: "/approvals",
    label: "Approvals",
    shortDesc: "Human-in-the-loop factual, editorial & visual QA sign-off",
    group: "production",
    targetPhase: "N2–N3 live",
    iconName: "shield-check",
  },
  {
    id: "analytics",
    href: "/analytics",
    label: "Analytics",
    shortDesc: "Platform-reported views and engagement for published posts",
    group: "performance",
    targetPhase: "N5 · views live",
    iconName: "line-chart",
  },
  {
    id: "integrations",
    href: "/integrations",
    label: "Integrations",
    shortDesc: "Postiz standalone boundary, service health & channel slots",
    group: "system",
    targetPhase: "N3 live",
    iconName: "plug",
  },
  {
    id: "agents",
    href: "/agents",
    label: "Agents",
    shortDesc: "Celery workers, transactional outbox, persistent scheduler & MCP",
    group: "system",
    targetPhase: "Phase 4 live",
    iconName: "bot",
  },
  {
    id: "settings",
    href: "/settings",
    label: "Settings",
    shortDesc: "Persisted BrandProfile specs & owner security posture",
    group: "system",
    targetPhase: "Live",
    iconName: "sliders-horizontal",
  },
  {
    id: "logs",
    href: "/logs",
    label: "Runtime Logs",
    shortDesc: "Sanitized request telemetry & correlation IDs",
    group: "system",
    targetPhase: "Live",
    iconName: "terminal",
  },
];

export function findRouteMeta(pathname: string): NavRouteItem {
  return NAV_ROUTES.find((item) => item.href === pathname) ?? NAV_ROUTES[0];
}
