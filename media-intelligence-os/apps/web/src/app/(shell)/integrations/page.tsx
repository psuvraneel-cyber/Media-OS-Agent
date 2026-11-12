import React from "react";

import { IntegrationsView } from "@/components/views/OperationsViews";
import { serverApiGet } from "@/lib/server-api";
import type { ChannelListResponse, SystemStatusResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function IntegrationsPage() {
  const [statusResult, channelsResult] = await Promise.all([
    serverApiGet<SystemStatusResponse>("/system/status"),
    serverApiGet<ChannelListResponse>("/channels"),
  ]);

  return (
    <IntegrationsView
      systemStatus={statusResult.ok ? statusResult.data : null}
      channels={channelsResult.ok ? channelsResult.data.items : []}
      errorMessage={
        !statusResult.ok
          ? statusResult.message
          : !channelsResult.ok
            ? channelsResult.message
            : undefined
      }
    />
  );
}
