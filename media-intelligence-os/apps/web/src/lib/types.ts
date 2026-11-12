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
