import { redirect } from "next/navigation";
import React, { Suspense } from "react";

import { OperatorShell } from "@/components/shell/OperatorShell";
import { ErrorBanner } from "@/components/ui/FeedbackStates";
import { BrandProvider } from "@/lib/brand-context";
import { serverApiGet } from "@/lib/server-api";
import type { BrandListResponse, SessionUser } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AuthenticatedShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const sessionResult = await serverApiGet<SessionUser>("/auth/me");
  if (!sessionResult.ok) {
    if (sessionResult.status === 401 || sessionResult.status === 403) {
      redirect("/login");
    }
    return (
      <div className="mx-auto max-w-2xl p-8">
        <ErrorBanner
          title="Control Plane Unavailable"
          message={sessionResult.message}
          code={sessionResult.code}
        />
      </div>
    );
  }

  const brandsResult = await serverApiGet<BrandListResponse>("/brands?include_inactive=true");
  if (!brandsResult.ok || brandsResult.data.items.length === 0) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <ErrorBanner
          title="Failed to Load Brand Context"
          message={brandsResult.ok ? "No brand profiles available." : brandsResult.message}
          code={brandsResult.ok ? "EMPTY_BRANDS" : brandsResult.code}
        />
      </div>
    );
  }

  return (
    <Suspense fallback={<div className="min-h-screen bg-[#090b10]" />}>
      <BrandProvider brands={brandsResult.data.items}>
        <OperatorShell sessionUser={sessionResult.data}>{children}</OperatorShell>
      </BrandProvider>
    </Suspense>
  );
}
