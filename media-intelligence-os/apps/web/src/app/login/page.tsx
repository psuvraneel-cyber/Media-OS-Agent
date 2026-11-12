import { redirect } from "next/navigation";
import React from "react";

import { LoginForm } from "@/components/views/LoginForm";
import { serverApiGet } from "@/lib/server-api";
import type { SessionUser } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function LoginPage() {
  const sessionResult = await serverApiGet<SessionUser>("/auth/me");
  if (sessionResult.ok) {
    redirect("/dashboard");
  }
  return <LoginForm />;
}
