import type { ApiErrorEnvelope } from "@/lib/types";

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;

  constructor(status: number, code: string, message: string, requestId = "") {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function readCsrfCookie(): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const cookies = document.cookie.split(";");
  for (const part of cookies) {
    const trimmed = part.trim();
    if (trimmed.startsWith("media_os_csrf=")) {
      return decodeURIComponent(trimmed.slice("media_os_csrf=".length));
    }
  }
  return null;
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function browserApiFetch<T>(
  apiPath: string,
  init: RequestInit & { csrfToken?: string } = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }

  if (MUTATING_METHODS.has(method)) {
    const csrf = init.csrfToken ?? readCsrfCookie();
    if (csrf && !headers.has("X-CSRF-Token")) {
      headers.set("X-CSRF-Token", csrf);
    }
  }

  const normalized = apiPath.startsWith("/") ? apiPath : `/${apiPath}`;
  const response = await fetch(`/api/v1${normalized}`, {
    ...init,
    method,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  if (!response.ok) {
    let parsed: ApiErrorEnvelope | null = null;
    try {
      parsed = (await response.json()) as ApiErrorEnvelope;
    } catch {
      parsed = null;
    }
    const err = parsed?.error;
    throw new ApiClientError(
      response.status,
      err?.code ?? `HTTP_${response.status}`,
      err?.message ?? "Request failed.",
      err?.request_id ?? response.headers.get("x-request-id") ?? "",
    );
  }

  return (await response.json()) as T;
}
