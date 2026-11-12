"use client";

import React from "react";
import { Layers } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { useBrandContext } from "@/lib/brand-context";

export function BrandSwitcher() {
  const { brands, activeBrand, selectBrand } = useBrandContext();

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-700/60 bg-[#0d1118] px-3 py-1.5">
      <Layers className="h-4 w-4 text-sky-400" aria-hidden="true" />
      <label htmlFor="brand-profile-select" className="text-[11px] font-medium text-slate-400">
        Brand Context
      </label>
      <select
        id="brand-profile-select"
        aria-label="Active Brand Profile"
        value={activeBrand.brand_id}
        onChange={(event) => selectBrand(event.target.value)}
        className="rounded border border-slate-700 bg-[#141b27] px-2.5 py-1 text-xs font-semibold text-slate-100 focus:border-sky-400 focus:outline-none"
      >
        {brands.map((brand) => (
          <option key={brand.brand_id} value={brand.brand_id}>
            {brand.name}
            {brand.placeholder_unconfigured ? " (Unconfigured)" : ""}
            {brand.active ? "" : " [Inactive]"}
          </option>
        ))}
      </select>
      <Badge tone={activeBrand.data_origin === "postgresql" ? "live" : "prototype"}>
        {activeBrand.data_origin === "postgresql"
          ? activeBrand.placeholder_unconfigured
            ? "PostgreSQL • Unconfigured"
            : "PostgreSQL"
          : "Prototype Fixture"}
      </Badge>
      {!activeBrand.active && <Badge tone="disabled">Inactive</Badge>}
    </div>
  );
}
