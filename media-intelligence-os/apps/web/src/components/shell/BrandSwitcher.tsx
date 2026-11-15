"use client";

import React from "react";
import { ChevronsUpDown } from "lucide-react";

import { useBrandContext } from "@/lib/brand-context";

/** Brand context selector. Every page's data is scoped to the brand chosen here. */
export function BrandSwitcher() {
  const { brands, activeBrand, selectBrand } = useBrandContext();
  const initials = activeBrand.name
    .split(/\s|&/)
    .filter(Boolean)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="glass-chip relative flex items-center gap-2 rounded-lg py-1 pr-2 pl-1 transition-colors focus-within:border-sky-400/50 hover:border-white/[0.2]">
      <span
        aria-hidden="true"
        className="flex h-6 w-6 items-center justify-center rounded-md bg-gradient-to-br from-sky-300/30 to-indigo-400/25 text-[10px] font-semibold text-sky-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.15)]"
      >
        {initials}
      </span>
      <label htmlFor="brand-profile-select" className="sr-only">
        Brand Context
      </label>
      <select
        id="brand-profile-select"
        aria-label="Active Brand Profile"
        value={activeBrand.brand_id}
        onChange={(event) => selectBrand(event.target.value)}
        className="max-w-[12rem] cursor-pointer appearance-none bg-transparent pr-5 text-xs font-medium text-slate-100 focus:outline-none"
      >
        {brands.map((brand) => (
          <option key={brand.brand_id} value={brand.brand_id} className="bg-[#151922]">
            {brand.name}
            {brand.placeholder_unconfigured ? " (Unconfigured)" : ""}
            {brand.active ? "" : " [Inactive]"}
          </option>
        ))}
      </select>
      <ChevronsUpDown
        className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-slate-500"
        aria-hidden="true"
      />
      {activeBrand.data_origin !== "postgresql" && (
        <span className="text-[10px] text-amber-300">Fixture</span>
      )}
    </div>
  );
}
