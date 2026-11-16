"use client";

import React from "react";

import { BrandLogo } from "@/components/shell/BrandLogo";
import { Dropdown } from "@/components/ui/Dropdown";
import { useBrandContext } from "@/lib/brand-context";

/** Brand context selector. Every page's data is scoped to the brand chosen here. */
export function BrandSwitcher() {
  const { brands, activeBrand, selectBrand } = useBrandContext();

  return (
    <div className="glass-chip relative flex items-center gap-1 rounded-lg py-0.5 pr-1.5 pl-1 transition-colors focus-within:border-sky-400/50 hover:border-white/[0.2]">
      <Dropdown
        id="brand-profile-select"
        ariaLabel="Active Brand Profile"
        variant="ghost"
        align="end"
        className="max-w-[14rem]"
        value={activeBrand.brand_id}
        onChange={selectBrand}
        options={brands.map((brand) => ({
          value: brand.brand_id,
          label: brand.name,
          hint: [brand.placeholder_unconfigured ? "Unconfigured" : "", brand.active ? "" : "Inactive"]
            .filter(Boolean)
            .join(" · ") || undefined,
          icon: <BrandLogo brand={brand} size={22} />,
        }))}
      />
      {activeBrand.data_origin !== "postgresql" && (
        <span className="text-[10px] text-amber-300">Fixture</span>
      )}
    </div>
  );
}
