import type { Metadata } from "next";
import { headers } from "next/headers";
import React from "react";

import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "Media Intelligence OS",
  description:
    "Local-first autonomous AI research and multi-brand social-media control plane.",
};

// Set by apps/desktop (Electron) on its User-Agent. Detected on the server so the transparent
// desktop styling is in the very first paint, with no flash of the browser background.
const DESKTOP_UA_TOKEN = "MediaOSDesktop/";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const userAgent = (await headers()).get("user-agent") ?? "";
  const shell = userAgent.includes(DESKTOP_UA_TOKEN) ? "desktop" : "browser";
  // Window look chosen in the desktop shell: "clear" (transparent, no blur) or "acrylic".
  const look = shell === "desktop" ? (/look\/(clear|acrylic)/.exec(userAgent)?.[1] ?? "acrylic") : undefined;

  return (
    <html lang="en" className="dark" data-shell={shell} data-look={look}>
      <body className="min-h-screen bg-[var(--surface-inset)] text-slate-100 antialiased">{children}</body>
    </html>
  );
}
