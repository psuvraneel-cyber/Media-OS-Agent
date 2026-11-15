import React from "react";

import { DashboardOverviewView } from "@/components/views/DashboardOverviewView";
import type { PipelineSummary, ViewsReport } from "@/lib/analytics";
import { DEFAULT_RANGE, pipelinePath, viewsPath } from "@/lib/analytics-paths";
import { serverApiGet } from "@/lib/server-api";
import type { SystemStatusResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ brand?: string }>;
}) {
  const { brand } = await searchParams;
  const vPath = brand ? viewsPath(brand, DEFAULT_RANGE) : null;
  const pPath = brand ? pipelinePath(brand) : null;
  // Fetch status, views and pipeline in parallel on the server; the client reuses them.
  const [statusResult, viewsResult, pipelineResult] = await Promise.all([
    serverApiGet<SystemStatusResponse>("/system/status"),
    vPath ? serverApiGet<ViewsReport>(vPath) : Promise.resolve(null),
    pPath ? serverApiGet<PipelineSummary>(pPath) : Promise.resolve(null),
  ]);
  return (
    <DashboardOverviewView
      systemStatus={statusResult.ok ? statusResult.data : null}
      errorMessage={statusResult.ok ? undefined : statusResult.message}
      initialViews={
        vPath && viewsResult
          ? {
              path: vPath,
              data: viewsResult.ok ? viewsResult.data : null,
              error: viewsResult.ok ? undefined : viewsResult.message,
            }
          : undefined
      }
      initialPipeline={
        pPath && pipelineResult
          ? {
              path: pPath,
              data: pipelineResult.ok ? pipelineResult.data : null,
              error: pipelineResult.ok ? undefined : pipelineResult.message,
            }
          : undefined
      }
    />
  );
}
