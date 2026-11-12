export type NavGroupId = "operations" | "production" | "intelligence";

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

export const NAV_GROUPS: ReadonlyArray<{ id: NavGroupId; label: string }> = [
  { id: "operations", label: "Intelligence" },
  { id: "production", label: "Production" },
  { id: "intelligence", label: "System & Ops" },
];

export const NAV_ROUTES: ReadonlyArray<NavRouteItem> = [
  {
    id: "dashboard",
    href: "/dashboard",
    label: "Overview",
    shortDesc: "Control plane status, phase readiness & brand workspace summary",
    group: "operations",
    targetPhase: "Phase 4 Active",
    iconName: "layout-dashboard",
  },
  {
    id: "research",
    href: "/research",
    label: "Research",
    shortDesc: "Approved source registry & topic discovery queue",
    group: "operations",
    targetPhase: "Phase 5 Target",
    iconName: "radar",
  },
  {
    id: "topics",
    href: "/topics",
    label: "Topics",
    shortDesc: "Editorial backlog, deduplication & story scoring rubric",
    group: "operations",
    targetPhase: "Phase 5-6 Target",
    iconName: "list-filter",
  },
  {
    id: "studio",
    href: "/studio",
    label: "Studio",
    shortDesc: "Script drafting, carousel layouts & narrated vertical video builder",
    group: "production",
    targetPhase: "Phase 7 Target",
    iconName: "clapperboard",
  },
  {
    id: "assets",
    href: "/assets",
    label: "Assets",
    shortDesc: "Brand-isolated media assets, renders & audio artifacts",
    group: "production",
    targetPhase: "Phase 7 Target",
    iconName: "folder-kanban",
  },
  {
    id: "calendar",
    href: "/calendar",
    label: "Calendar",
    shortDesc: "Brand-scoped Instagram & YouTube publishing cadence",
    group: "production",
    targetPhase: "Phase 8 Target",
    iconName: "calendar-clock",
  },
  {
    id: "approvals",
    href: "/approvals",
    label: "Approvals",
    shortDesc: "Human-in-the-loop factual, editorial & visual QA sign-off",
    group: "production",
    targetPhase: "Phase 6-8 Target",
    iconName: "shield-check",
  },
  {
    id: "analytics",
    href: "/analytics",
    label: "Analytics",
    shortDesc: "Postiz performance ingestion & feedback learning signals",
    group: "intelligence",
    targetPhase: "Phase 9 Target",
    iconName: "line-chart",
  },
  {
    id: "agents",
    href: "/agents",
    label: "Agents",
    shortDesc: "Celery workers, transactional outbox, persistent scheduler & MCP",
    group: "intelligence",
    targetPhase: "Phase 4 Active",
    iconName: "bot",
  },
  {
    id: "integrations",
    href: "/integrations",
    label: "Integrations",
    shortDesc: "Postiz standalone boundary, service health & channel slots",
    group: "intelligence",
    targetPhase: "Phase 4 / 8",
    iconName: "plug",
  },
  {
    id: "settings",
    href: "/settings",
    label: "Settings",
    shortDesc: "Persisted BrandProfile specs & owner security posture",
    group: "intelligence",
    targetPhase: "Phase 4 Live",
    iconName: "sliders-horizontal",
  },
  {
    id: "logs",
    href: "/logs",
    label: "Runtime Logs",
    shortDesc: "Sanitized request telemetry & correlation IDs",
    group: "intelligence",
    targetPhase: "Phase 4 Live",
    iconName: "terminal",
  },
];

export function findRouteMeta(pathname: string): NavRouteItem {
  return NAV_ROUTES.find((item) => item.href === pathname) ?? NAV_ROUTES[0];
}
