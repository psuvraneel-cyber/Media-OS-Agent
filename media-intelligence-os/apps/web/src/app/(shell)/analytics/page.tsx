import React from "react";

import { AnalyticsView } from "@/components/views/AnalyticsView";
import type { PipelineSummary, PostsPage, ViewsReport } from "@/lib/analytics";
import type { AccountReport } from "@/components/analytics/AccountTotals";
import type { LearningReport } from "@/components/analytics/LearningPanel";
import {
  DEFAULT_RANGE,
  accountPath,
  learningPath,
  pipelinePath,
  postsPath,
  viewsPath,
} from "@/lib/analytics-paths";
import { serverApiGet, type ServerFetchResult } from "@/lib/server-api";

export const dynamic = "force-dynamic";

function seed<T>(path: string | null, result: ServerFetchResult<T> | null) {
  if (!path || !result) return undefined;
  return result.ok
    ? { path, data: result.data }
    : { path, data: null, error: result.message };
}

export default async function AnalyticsPage({
  searchParams,
}: {
  searchParams: Promise<{ brand?: string }>;
}) {
  const { brand } = await searchParams;
  const vPath = brand ? viewsPath(brand, DEFAULT_RANGE, { platform: "", account: "", format: "" }) : null;
  const lPath = brand
    ? postsPath(brand, DEFAULT_RANGE, "views", 1, { platform: "", account: "", format: "" }, 10)
    : null;
  const pPath = brand ? pipelinePath(brand) : null;
  const aPath = brand ? accountPath(brand) : null;
  const learnPath = brand ? learningPath(brand) : null;
  const [views, posts, pipeline, account, learning] = await Promise.all([
    vPath ? serverApiGet<ViewsReport>(vPath) : Promise.resolve(null),
    lPath ? serverApiGet<PostsPage>(lPath) : Promise.resolve(null),
    pPath ? serverApiGet<PipelineSummary>(pPath) : Promise.resolve(null),
    aPath ? serverApiGet<AccountReport>(aPath) : Promise.resolve(null),
    learnPath ? serverApiGet<LearningReport>(learnPath) : Promise.resolve(null),
  ]);
  return (
    <AnalyticsView
      initialViews={seed(vPath, views)}
      initialPosts={seed(lPath, posts)}
      initialPipeline={seed(pPath, pipeline)}
      initialAccount={seed(aPath, account)}
      initialLearning={seed(learnPath, learning)}
    />
  );
}
