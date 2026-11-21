"use client";

import React, { useState } from "react";
import { Brain, Clock, Lightbulb, Target } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { ErrorBanner } from "@/components/ui/FeedbackStates";
import { SurfaceCard } from "@/components/ui/SurfaceCard";
import { useApiResource, type Initial } from "@/lib/analytics";
import { learningPath } from "@/lib/analytics-paths";

export interface LearningGroup {
  value: string;
  n: number;
  median_lift: number | null;
  avg_audience: number | null;
  avg_engagement_rate: number | null;
  verdict: "BETTER" | "WORSE" | "NO_CLEAR_DIFFERENCE" | "NOT_ENOUGH_DATA";
  needed: number;
}

export interface LearningPost {
  schedule_id: string;
  package_id: string;
  platform: string;
  title: string;
  published_at: string;
  mature: boolean;
  audience_72h: number | null;
  audience_metric?: string | null;
  baseline_audience: number | null;
  actual_lift: number | null;
  predicted_lift: number | null;
  v0_score: number | null;
  features: Record<string, string>;
}

export interface LearningReport {
  platform: string;
  audience_note?: string;
  settings: { min_posts: number; mature_after_hours: number; baseline_posts: number; explore_share: number };
  counts: { published: number; measured: number; waiting_for_72h: number };
  model: {
    active: boolean;
    n: number;
    mae_model: number | null;
    mae_baseline: number | null;
    rank_corr: number | null;
    v0_rank_corr: number | null;
  };
  groups: Record<string, LearningGroup[]>;
  winners: Record<string, string[]>;
  best_times: { recommended: string[]; by_hour: LearningGroup[]; by_slot: LearningGroup[]; by_weekday: LearningGroup[] };
  winning_hooks: { title: string; lift: number; package_id: string }[];
  posts: LearningPost[];
}

const DIMENSIONS: { key: string; label: string }[] = [
  { key: "format", label: "Format" },
  { key: "pillar", label: "Topic" },
  { key: "hook_type", label: "Hook" },
  { key: "language", label: "Language" },
  { key: "occasion", label: "Occasion" },
  { key: "design", label: "Design" },
  { key: "slot", label: "Time of day" },
  { key: "weekday", label: "Day" },
];

const AUDIENCE_WORD: Record<string, string> = { unique_viewers: "reached", plays: "plays" };

const LANGUAGE: Record<string, string> = { EN: "English", BN: "Bengali", BN_EN: "Bilingual" };

export function nice(value: string): string {
  if (LANGUAGE[value]) return LANGUAGE[value];
  if (value === value.toUpperCase() || value.includes("_")) {
    const s = value.replace(/_/g, " ").toLowerCase();
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  return value;
}

export function lift(x: number | null): string {
  return x === null ? "–" : `${x.toFixed(x >= 10 ? 0 : 1)}×`;
}

function verdictBadge(g: LearningGroup, minPosts: number): { tone: BadgeTone; text: string } {
  switch (g.verdict) {
    case "BETTER":
      return { tone: "live", text: "Works better" };
    case "WORSE":
      return { tone: "blocked", text: "Works worse" };
    case "NO_CLEAR_DIFFERENCE":
      return { tone: "neutral", text: "No clear difference" };
    default:
      return { tone: "disabled", text: `Not enough data yet (${g.n}/${minPosts})` };
  }
}

function liftTone(x: number | null): string {
  if (x === null) return "text-slate-500";
  if (x >= 1.2) return "text-emerald-300";
  if (x <= 0.8) return "text-rose-300";
  return "text-slate-200";
}

function isReport(x: unknown): x is LearningReport {
  const r = x as LearningReport | null;
  return Boolean(r && r.settings && r.counts && r.model && r.groups && r.best_times && r.posts);
}

export function LearningPanel({
  brandId,
  initial,
}: {
  brandId: string;
  initial?: Initial<LearningReport>;
}) {
  const [dimension, setDimension] = useState("format");
  const [platform, setPlatform] = useState<"INSTAGRAM" | "FACEBOOK">("INSTAGRAM");
  const { data, error } = useApiResource<LearningReport>(learningPath(brandId, platform), initial);

  if (error) return <ErrorBanner title="Could not load what's working" message={error} />;
  if (!isReport(data)) return null;
  // Until the selected platform's report arrives, keep showing the switch but not the old numbers.
  const stale = data.platform !== platform;
  const report = data;
  const audienceHeader = platform === "FACEBOOK" ? "Avg audience (72 h)" : "Avg views (72 h)";

  const minPosts = report.settings.min_posts;
  const groups = report.groups[dimension] ?? [];
  const m = report.model;
  const accuracy =
    m.mae_model !== null && m.mae_baseline !== null
      ? m.mae_model < m.mae_baseline
        ? "beats a plain guess"
        : "doesn't beat a plain guess yet"
      : null;

  const platformSwitch = (
    <div className="mb-3 flex flex-wrap items-center gap-2 text-xs" role="radiogroup" aria-label="Platform">
      {(["INSTAGRAM", "FACEBOOK"] as const).map((p) => (
        <button
          key={p}
          type="button"
          role="radio"
          aria-checked={platform === p}
          onClick={() => setPlatform(p)}
          className={`rounded-md border px-2.5 py-1 ${
            platform === p
              ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200"
              : "border-white/[0.08] text-slate-400 hover:text-slate-200"
          }`}
        >
          {p === "INSTAGRAM" ? "Instagram" : "Facebook"}
        </button>
      ))}
      {!stale && report.audience_note ? <span className="text-slate-500">{report.audience_note}</span> : null}
    </div>
  );

  if (stale) {
    return (
      <SurfaceCard icon={<Lightbulb />} title="What's working">
        {platformSwitch}
        <p className="text-xs text-slate-400">
          Loading {platform === "FACEBOOK" ? "Facebook" : "Instagram"} results…
        </p>
      </SurfaceCard>
    );
  }

  return (
    <div className="space-y-6">
      <SurfaceCard
        icon={<Lightbulb />}
        title="What's working"
        subtitle={`Each post is measured at ${report.settings.mature_after_hours} hours and compared with your usual post (the median of the previous ${report.settings.baseline_posts}). 2× = twice your usual views.`}
        badge={
          <Badge tone="info">
            {report.counts.measured} measured · {report.counts.waiting_for_72h} waiting for {report.settings.mature_after_hours} h
          </Badge>
        }
      >
        {platformSwitch}
        <div className="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label="Compare by">
          {DIMENSIONS.map((d) => (
            <button
              key={d.key}
              type="button"
              role="tab"
              aria-selected={dimension === d.key}
              onClick={() => setDimension(d.key)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                dimension === d.key
                  ? "border-sky-400/40 bg-sky-400/10 text-sky-200"
                  : "border-white/[0.08] text-slate-400 hover:text-slate-200"
              }`}
            >
              {d.label}
            </button>
          ))}
        </div>
        {groups.length === 0 ? (
          <p className="text-xs text-slate-400">
            No post has reached {report.settings.mature_after_hours} hours old yet, so there&apos;s nothing to compare.
          </p>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-left text-slate-500">
              <tr>
                <th scope="col" className="py-1.5 font-medium">{DIMENSIONS.find((d) => d.key === dimension)?.label}</th>
                <th scope="col" className="py-1.5 text-right font-medium">Posts</th>
                <th scope="col" className="py-1.5 text-right font-medium">vs usual</th>
                <th scope="col" className="py-1.5 text-right font-medium">{audienceHeader}</th>
                <th scope="col" className="py-1.5 pl-3 font-medium">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const v = verdictBadge(g, minPosts);
                return (
                  <tr key={g.value} className="border-t border-white/[0.05]">
                    <td className="py-1.5 text-slate-200">{nice(g.value)}</td>
                    <td className="py-1.5 text-right text-slate-300">{g.n}</td>
                    <td className={`py-1.5 text-right font-medium ${liftTone(g.median_lift)}`}>{lift(g.median_lift)}</td>
                    <td className="py-1.5 text-right text-slate-300">{g.avg_audience ?? "–"}</td>
                    <td className="py-1.5 pl-3">
                      <Badge tone={v.tone}>{v.text}</Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <p className="mt-3 text-[11px] text-slate-500">
          A verdict needs {minPosts} measured posts in a group; below that the numbers are an early signal, not a
          conclusion. About {Math.round(report.settings.explore_share * 100)}% of generated posts stay experiments;
          the rest use proven winners once there are any.
        </p>
      </SurfaceCard>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SurfaceCard
          icon={<Brain />}
          title="Learned score"
          subtitle="Trained only on your own posts"
          badge={m.active ? <Badge tone="live">In use</Badge> : <Badge tone="disabled">Not trusted yet</Badge>}
        >
          <ul className="space-y-1.5 text-xs text-slate-300">
            <li>
              Learned from <strong>{m.n}</strong> measured posts{m.active ? "" : `; it's used once it has ${minPosts} and beats a plain guess`}.
            </li>
            {accuracy ? <li>Tested on each past post without seeing it: it {accuracy}.</li> : null}
            {m.rank_corr !== null ? (
              <li>
                Ranking accuracy {m.rank_corr.toFixed(2)} (1 = perfect order, 0 = random)
                {m.v0_rank_corr !== null ? `; the old heuristic score: ${m.v0_rank_corr.toFixed(2)}` : ""}.
              </li>
            ) : null}
          </ul>
        </SurfaceCard>

        <SurfaceCard icon={<Clock />} title="Best posting times" subtitle="From how your own posts did by hour">
          {report.best_times.recommended.length > 0 ? (
            <p className="text-sm text-emerald-200">Proven: {report.best_times.recommended.join(", ")}</p>
          ) : (
            <p className="text-xs text-slate-400">
              Nothing proven yet ({minPosts} posts per hour needed). Early numbers:
            </p>
          )}
          <ul className="mt-2 space-y-1 text-xs">
            {report.best_times.by_hour.slice(0, 5).map((g) => (
              <li key={g.value} className="flex justify-between text-slate-300">
                <span>{g.value}</span>
                <span>
                  <span className={liftTone(g.median_lift)}>{lift(g.median_lift)}</span>{" "}
                  <span className="text-slate-500">· {g.n} posts</span>
                </span>
              </li>
            ))}
          </ul>
        </SurfaceCard>
      </div>

      <SurfaceCard icon={<Target />} title="Predicted vs actual" subtitle="Every post, measured at 72 hours" flush>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-slate-500">
              <tr>
                <th scope="col" className="px-5 py-2 font-medium">Post</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {platform === "FACEBOOK" ? "Audience (72 h)" : "Views (72 h)"}
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Usual</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Actual</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Predicted</th>
                <th scope="col" className="px-5 py-2 text-right font-medium">Old score</th>
              </tr>
            </thead>
            <tbody>
              {report.posts.map((p) => (
                <tr key={p.schedule_id} className="border-t border-white/[0.05]">
                  <td className="max-w-[18rem] px-5 py-2">
                    <p className="bn truncate text-slate-200" lang="bn">{p.title}</p>
                    <p className="text-[11px] text-slate-500">
                      {nice(p.features.format ?? "")}
                      {p.features.hook_type && !["UNKNOWN", "OWNER_UPLOAD"].includes(p.features.hook_type)
                        ? ` · ${nice(p.features.hook_type)}`
                        : ""}
                      {p.features.weekday ? ` · ${p.features.weekday} ${p.features.hour ?? ""}` : ""}
                    </p>
                  </td>
                  {p.mature ? (
                    <>
                      <td className="px-3 py-2 text-right text-slate-300">
                        {p.audience_72h ?? "–"}
                        {p.audience_metric && p.audience_metric !== "views" && p.audience_72h !== null ? (
                          <span className="ml-1 text-[10px] text-slate-500">{AUDIENCE_WORD[p.audience_metric] ?? ""}</span>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-500">{p.baseline_audience ?? "–"}</td>
                      <td className={`px-3 py-2 text-right font-medium ${liftTone(p.actual_lift)}`}>{lift(p.actual_lift)}</td>
                      <td className="px-3 py-2 text-right text-slate-300">{lift(p.predicted_lift)}</td>
                    </>
                  ) : (
                    <td colSpan={4} className="px-3 py-2 text-right text-slate-500">
                      Measured once it&apos;s {report.settings.mature_after_hours} hours old
                    </td>
                  )}
                  <td className="px-5 py-2 text-right text-slate-500">{p.v0_score ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SurfaceCard>

      {report.winning_hooks.length > 0 ? (
        <SurfaceCard icon={<Lightbulb />} title="Hooks given to the writer as examples" subtitle="Your best-performing hooks (style only, never copied)">
          <ul className="space-y-1 text-xs text-slate-300">
            {report.winning_hooks.map((h) => (
              <li key={h.package_id} className="flex justify-between gap-3">
                <span className="bn truncate" lang="bn">{h.title}</span>
                <span className="text-emerald-300">{lift(h.lift)}</span>
              </li>
            ))}
          </ul>
        </SurfaceCard>
      ) : null}
    </div>
  );
}
