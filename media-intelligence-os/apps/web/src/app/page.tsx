import { redirect } from "next/navigation";

import { serverApiGet } from "@/lib/server-api";
import type { SessionUser } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function RootPage() {
  const sessionResult = await serverApiGet<SessionUser>("/auth/me");
  if (sessionResult.ok) {
    redirect("/dashboard");
  }
  redirect("/login");
}
