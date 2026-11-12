"use client";

import React, { createContext, useCallback, useContext, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { BrandProfileSummary } from "@/lib/types";

interface BrandContextValue {
  brands: BrandProfileSummary[];
  activeBrand: BrandProfileSummary;
  selectBrand: (brandId: string) => void;
  buildHrefWithBrand: (pathname: string) => string;
}

const BrandContext = createContext<BrandContextValue | null>(null);

interface BrandProviderProps {
  brands: BrandProfileSummary[];
  initialBrandId?: string;
  children: React.ReactNode;
}

function findMatchingBrand(
  brands: BrandProfileSummary[],
  candidate: string | null | undefined,
): BrandProfileSummary | undefined {
  if (!candidate) {
    return undefined;
  }
  return brands.find((b) => b.brand_id === candidate || b.slug === candidate);
}

export function BrandProvider({ brands, initialBrandId, children }: BrandProviderProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const urlBrand = searchParams.get("brand");
  const fallbackId = brands[0]?.brand_id ?? "brand-tech-ai";
  const [selectedId, setSelectedId] = useState<string>(() => {
    const matched = findMatchingBrand(brands, urlBrand ?? initialBrandId);
    return matched ? matched.brand_id : fallbackId;
  });

  const resolvedId = useMemo(() => {
    const urlMatch = findMatchingBrand(brands, urlBrand);
    if (urlMatch) {
      return urlMatch.brand_id;
    }
    const stateMatch = findMatchingBrand(brands, selectedId);
    if (stateMatch) {
      return stateMatch.brand_id;
    }
    return fallbackId;
  }, [urlBrand, selectedId, brands, fallbackId]);

  const activeBrand = useMemo(() => {
    return brands.find((b) => b.brand_id === resolvedId) ?? brands[0];
  }, [brands, resolvedId]);

  const selectBrand = useCallback(
    (brandRef: string) => {
      const target = findMatchingBrand(brands, brandRef);
      if (!target) {
        return;
      }
      setSelectedId(target.brand_id);
      const params = new URLSearchParams(searchParams.toString());
      params.set("brand", target.brand_id);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [brands, pathname, router, searchParams],
  );

  const buildHrefWithBrand = useCallback(
    (targetPath: string) => `${targetPath}?brand=${encodeURIComponent(activeBrand.brand_id)}`,
    [activeBrand.brand_id],
  );

  const value = useMemo<BrandContextValue>(
    () => ({
      brands,
      activeBrand,
      selectBrand,
      buildHrefWithBrand,
    }),
    [brands, activeBrand, selectBrand, buildHrefWithBrand],
  );

  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

export function useBrandContext(): BrandContextValue {
  const ctx = useContext(BrandContext);
  if (!ctx) {
    throw new Error("useBrandContext must be used within BrandProvider");
  }
  return ctx;
}
