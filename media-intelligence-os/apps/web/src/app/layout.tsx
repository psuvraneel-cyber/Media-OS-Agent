import type { Metadata } from "next";
import React from "react";

import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "Media Intelligence OS",
  description:
    "Local-first autonomous AI research and multi-brand social-media control plane (Phase 3 Foundation).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-[var(--surface-inset)] text-slate-100 antialiased">{children}</body>
    </html>
  );
}
