"use client";

import React, { useState } from "react";

import { CarouselReviewView } from "@/components/views/CarouselReviewView";
import { PublishQueueView } from "@/components/views/PublishQueueView";

const TABS = [
  { id: "review", label: "Review" },
  { id: "publish", label: "Approved — ready to post" },
] as const;

export function ApprovalsTabs() {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("review");
  return (
    <div className="space-y-4">
      <div role="tablist" aria-label="Approvals" className="flex gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-md px-3 py-1.5 text-sm ${
              tab === t.id
                ? "bg-amber-500/15 text-amber-200 border border-amber-500/40"
                : "border border-white/[0.1] text-slate-300"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "review" ? <CarouselReviewView /> : <PublishQueueView />}
    </div>
  );
}
