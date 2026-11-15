import React from "react";

import { CalendarView } from "@/components/views/CalendarView";

export const dynamic = "force-dynamic";

export default function CalendarPage() {
  // Browser-facing Postiz address (the API reaches Postiz separately via host.docker.internal).
  const postizUrl = process.env.POSTIZ_PUBLIC_URL?.trim() || "http://localhost:4007";
  return <CalendarView postizUrl={postizUrl} />;
}
