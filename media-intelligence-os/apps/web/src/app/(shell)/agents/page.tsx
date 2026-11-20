import React from "react";

import { AgentsView } from "@/components/views/PipelineStubViews";
import { serverApiGet } from "@/lib/server-api";
import type { ProcessStatusResponse, SystemStatusResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  const [statusResult, processResult] = await Promise.all([
    serverApiGet<SystemStatusResponse>("/system/status"),
    serverApiGet<ProcessStatusResponse>("/system/processes"),
  ]);
  return (
    <AgentsView
      systemStatus={statusResult.ok ? statusResult.data : null}
      processes={processResult.ok ? processResult.data.items : null}
    />
  );
}
