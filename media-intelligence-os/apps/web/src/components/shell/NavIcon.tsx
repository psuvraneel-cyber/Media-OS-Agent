import React from "react";
import {
  Bot,
  CalendarClock,
  Clapperboard,
  FolderKanban,
  LayoutDashboard,
  LineChart,
  ListFilter,
  Plug,
  Radar,
  ShieldCheck,
  SlidersHorizontal,
  Terminal,
} from "lucide-react";

import type { NavRouteItem } from "@/lib/navigation";

export function NavIcon({
  name,
  className = "h-4 w-4",
}: {
  name: NavRouteItem["iconName"];
  className?: string;
}) {
  switch (name) {
    case "layout-dashboard":
      return <LayoutDashboard className={className} aria-hidden="true" />;
    case "radar":
      return <Radar className={className} aria-hidden="true" />;
    case "list-filter":
      return <ListFilter className={className} aria-hidden="true" />;
    case "clapperboard":
      return <Clapperboard className={className} aria-hidden="true" />;
    case "folder-kanban":
      return <FolderKanban className={className} aria-hidden="true" />;
    case "calendar-clock":
      return <CalendarClock className={className} aria-hidden="true" />;
    case "shield-check":
      return <ShieldCheck className={className} aria-hidden="true" />;
    case "line-chart":
      return <LineChart className={className} aria-hidden="true" />;
    case "bot":
      return <Bot className={className} aria-hidden="true" />;
    case "plug":
      return <Plug className={className} aria-hidden="true" />;
    case "sliders-horizontal":
      return <SlidersHorizontal className={className} aria-hidden="true" />;
    case "terminal":
      return <Terminal className={className} aria-hidden="true" />;
  }
}
