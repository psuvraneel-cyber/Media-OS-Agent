import { cookies } from "next/headers";

import type { ApiErrorEnvelope } from "@/lib/types";

export function getInternalApiBaseUrl(): string {
  const configured = process.env.INTERNAL_API_BASE_URL?.trim();
  return (configured && configured.length > 0 ? configured : "http://127.0.0.1:8000").replace(
    /\/+$/,
    "",
  );
}

export type ServerFetchResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; code: string; message: string };

export async function serverApiGet<T>(apiPath: string): Promise<ServerFetchResult<T>> {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore.toString();
  const normalized = apiPath.startsWith("/") ? apiPath : `/${apiPath}`;

  try {
    const response = await fetch(`${getInternalApiBaseUrl()}/api/v1${normalized}`, {
      method: "GET",
      headers: cookieHeader ? { Cookie: cookieHeader } : undefined,
      cache: "no-store",
    });

    if (!response.ok) {
      let parsed: ApiErrorEnvelope | null = null;
      try {
        parsed = (await response.json()) as ApiErrorEnvelope;
      } catch {
        parsed = null;
      }
      return {
        ok: false,
        status: response.status,
        code: parsed?.error?.code ?? `HTTP_${response.status}`,
        message: parsed?.error?.message ?? "Upstream API request failed.",
      };
    }

    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch {
    return {
      ok: false,
      status: 503,
      code: "API_UNREACHABLE",
      message: "FastAPI control-plane backend is currently unreachable.",
    };
  }
}
