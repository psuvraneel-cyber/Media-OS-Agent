import React from "react";

import { SettingsView } from "@/components/views/OperationsViews";
import { ErrorBanner } from "@/components/ui/FeedbackStates";
import { serverApiGet } from "@/lib/server-api";
import type { SessionUser, SystemStatusResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const [sessionResult, statusResult] = await Promise.all([
    serverApiGet<SessionUser>("/auth/me"),
    serverApiGet<SystemStatusResponse>("/system/status"),
  ]);

  if (!sessionResult.ok) {
    return <ErrorBanner message={sessionResult.message} code={sessionResult.code} />;
  }

  return (
    <SettingsView
      sessionUser={sessionResult.data}
      systemStatus={statusResult.ok ? statusResult.data : null}
    />
  );
}
