export type BrandId = string;

export interface ApiErrorDetail {
  code: string;
  message: string;
  request_id: string;
  details?: unknown;
}

export interface ApiErrorEnvelope {
  error: ApiErrorDetail;
}

export interface SessionUser {
  authenticated: true;
  username: string;
  role: "owner";
  csrf_token: string;
  expires_at_utc: string;
  phase: string;
}

export interface BrandProfileSummary {
  brand_id: string;
  slug: string;
  name: string;
  niche: string;
  description: string;
  target_audience: string;
  language: string;
  tone_of_voice: string;
  content_pillars: string[];
  visual_identity: Record<string, unknown>;
  timezone: string;
  approval_policy: "human_required";
  placeholder_unconfigured: boolean;
  active: boolean;
  data_origin: "postgresql" | "phase3_prototype_fixture";
  created_at_utc?: string | null;
  updated_at_utc?: string | null;
}

export interface BrandListResponse {
  is_prototype_fixture: boolean;
  persistent: boolean;
  items: BrandProfileSummary[];
}

export interface ChannelAccountSummary {
  channel_id: string;
  brand_id: string;
  platform: "instagram" | "youtube";
  display_label: string;
  external_username?: string | null;
  connection_status: string;
  publishing_enabled: false;
  postiz_integration_id: string | null;
  data_origin: "postgresql" | "phase3_prototype_fixture";
}

export interface ChannelListResponse {
  is_prototype_fixture: boolean;
  persistent: boolean;
  items: ChannelAccountSummary[];
}

export interface PostizReachabilitySummary {
  status: "reachable" | "unreachable" | "error";
  checked_at_utc: string;
  detail: string;
  api_credentials_managed_in_phase: "phase-8";
}

export interface ModuleBoundaryStatus {
  module_id: string;
  title: string;
  status: "active_phase_3" | "active_phase_4" | "not_implemented" | "blocked_by_policy";
  target_phase: string;
  summary: string;
}

export interface SystemStatusResponse {
  service: string;
  phase: string;
  environment: string;
  display_timezone: string;
  auth_mode: "single_owner_postgresql_cookie" | "single_owner_bootstrap_cookie";
  persistent_storage: boolean;
  database_status: "ready" | "unreachable" | "not_configured";
  redis_status: "ready" | "unreachable" | "not_configured";
  schema_version?: string | null;
  csrf_protection: true;
  postiz: PostizReachabilitySummary;
  modules: ModuleBoundaryStatus[];
  timestamp_utc: string;
}

export interface TelemetryEventItem {
  timestamp_utc: string;
  request_id: string;
  method: string;
  route: string;
  status_code: number;
  duration_ms: number;
  error_category: string | null;
}

export interface TelemetryLogResponse {
  ephemeral: true;
  persistent: false;
  note: string;
  items: TelemetryEventItem[];
}

export interface AuditEventItem {
  id: string;
  timestamp_utc: string;
  actor_id: string;
  action: string;
  target_type: string;
  target_id: string;
  request_id: string;
  outcome: string;
  metadata: Record<string, unknown>;
}

export interface AuditLogResponse {
  persistent: true;
  items: AuditEventItem[];
}

// ---------------------------------------------------------------- Research (Phase 5, M6)

export interface ResearchCheckpointSummary {
  stream: string;
  last_fetched_at_utc: string | null;
  last_success_at_utc: string | null;
  conditional_request_ready: boolean;
}

export interface ResearchScheduleSummary {
  enabled: boolean;
  next_run_at_utc: string | null;
  cadence: Record<string, unknown>;
}

export interface ResearchSourceSummary {
  source: string;
  display_name: string;
  policy_mode: "OFF" | "EXPERIMENTAL" | "ENABLED" | "BLOCKED" | string;
  live_fetch_approved: boolean;
  approved_at_utc: string | null;
  health_status: string;
  consecutive_failures: number;
  last_attempt_at_utc: string | null;
  last_success_at_utc: string | null;
  last_failure_at_utc: string | null;
  last_error_code: string | null;
  backoff_until_utc: string | null;
  last_run_totals: Record<string, number>;
  schedule: ResearchScheduleSummary | null;
  checkpoints: ResearchCheckpointSummary[];
}

export interface ResearchSourceListResponse {
  live_fetch_kill_switch_enabled: boolean;
  items: ResearchSourceSummary[];
}

export interface ResearchClusterRef {
  topic_cluster_id: string;
  dedupe_key: string;
  discovery_status: string;
}

export interface ResearchSourceItem {
  source_item_id: string;
  provider: string;
  external_id: string;
  title: string;
  source_label: string;
  canonical_url: string;
  published_at_utc: string | null;
  fetched_at_utc: string;
  summary: string | null;
  source_policy_mode: string;
  clusters: ResearchClusterRef[];
}

export interface ResearchItemListResponse {
  total: number;
  next_before_utc: string | null;
  items: ResearchSourceItem[];
}

export interface BrandAssignmentSummary {
  brand_id: string;
  editorial_status: string;
  relevance_score: number | null;
  priority: number;
  assigned_at_utc: string;
  evaluated_at_utc: string | null;
}

export interface TopicSummary {
  topic_cluster_id: string;
  title: string;
  category: string;
  dedupe_key: string;
  discovery_status: string;
  first_seen_at_utc: string;
  last_seen_at_utc: string;
  source_count: number;
  providers: string[];
  assignment: BrandAssignmentSummary;
}

export interface TopicListResponse {
  brand_id: string;
  total: number;
  items: TopicSummary[];
}

export interface TopicDetailResponse {
  topic: TopicSummary;
  sources: ResearchSourceItem[];
}

// N2 carousel review queue
export interface QaCheck {
  name: string;
  status: "PASS" | "WARN" | "FAIL" | string;
  detail: string;
}

export interface CarouselSummary {
  id: string;
  status: string;
  approval_status: string;
  title: string | null;
  hook: string | null;
  language: string | null;
  pillar: string | null;
  hook_type: string | null;
  slide_count: number;
  qa_passed: boolean | null;
  qa_warnings: number;
  score_total: number | null;
  score_label: string | null;
  thumbnail_asset_id: string | null;
  created_at: string;
  schedules: ScheduleEntry[];
  scheduling: boolean;
}

export interface ScheduleEntry {
  platform: string;
  status: string;
  scheduled_at: string;
  timezone: string;
  post_url: string | null;
}

export interface CarouselListResponse {
  items: CarouselSummary[];
}

export interface CarouselDetail extends CarouselSummary {
  caption: string | null;
  hashtags: string[];
  alt_text: string | null;
  slides: Record<string, unknown>[];
  assets: { id: string; index: number; width: number | null; height: number | null }[];
  qa_report: { passed?: boolean; checks?: QaCheck[] } | null;
  score: { total?: number; label?: string; suggestion?: string | null } | null;
  post_tags: Record<string, unknown> | null;
}
