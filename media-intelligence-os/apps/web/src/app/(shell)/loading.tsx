import React from "react";

import { BrandLoader } from "@/components/shell/BrandLoader";

// Shown inside the shell (sidebar and header stay put) while a page's server data loads.
export default function ShellLoading() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <BrandLoader label="Loading" detail="Fetching the latest from your Media OS stack" />
    </div>
  );
}
