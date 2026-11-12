import React from "react";

import { DashboardOverviewView } from "@/components/views/DashboardOverviewView";
import { serverApiGet } from "@/lib/server-api";
import type { SystemStatusResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const statusResult = await serverApiGet<SystemStatusResponse>("/system/status");
  return (
    <DashboardOverviewView
      systemStatus={statusResult.ok ? statusResult.data : null}
      errorMessage={statusResult.ok ? undefined : statusResult.message}
    />
  );
}
