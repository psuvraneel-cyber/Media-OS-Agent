import { NextRequest, NextResponse } from "next/server";

import { getInternalApiBaseUrl } from "@/lib/server-api";

function isMediaType(contentType: string | null): boolean {
  return /^(video|image)\//.test(contentType ?? "") || contentType === "application/octet-stream";
}

async function proxyRequest(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await context.params;
  if (path.some((segment) => segment === "." || segment === ".." || segment.includes("\\"))) {
    return NextResponse.json(
      {
        error: {
          code: "INVALID_PATH",
          message: "Invalid API proxy path segment.",
          request_id: request.headers.get("x-request-id") ?? "invalid-path",
          details: null,
        },
      },
      { status: 400 },
    );
  }
  const targetPath = `/api/v1/${path.join("/")}`;
  const search = request.nextUrl.search;
  const targetUrl = `${getInternalApiBaseUrl()}${targetPath}${search}`;

  const forwardHeaders = new Headers();
  for (const name of ["content-type", "cookie", "x-csrf-token", "x-request-id", "origin", "range"]) {
    const val = request.headers.get(name);
    if (val) {
      forwardHeaders.set(name, val);
    }
  }

  const method = request.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD";
  // Uploads (reels, images) are streamed straight through, so a large video never sits in memory.
  const streamUpload = hasBody && isMediaType(request.headers.get("content-type")) && request.body !== null;
  const body = !hasBody ? undefined : streamUpload ? request.body : await request.arrayBuffer();

  try {
    const upstream = await fetch(targetUrl, {
      method,
      headers: forwardHeaders,
      body,
      redirect: "manual",
      cache: "no-store",
      ...(streamUpload ? { duplex: "half" } : {}),
    } as RequestInit);

    const outHeaders = new Headers();
    const contentType = upstream.headers.get("content-type");
    if (contentType) {
      outHeaders.set("content-type", contentType);
    }
    // Videos stream back with Range support, so the player can seek without a full download.
    const streamDownload = contentType?.startsWith("video/") ?? false;
    if (streamDownload) {
      for (const name of ["content-length", "content-range", "accept-ranges", "cache-control"]) {
        const val = upstream.headers.get(name);
        if (val) {
          outHeaders.set(name, val);
        }
      }
    }
    const responseBody = streamDownload ? upstream.body : await upstream.arrayBuffer();
    const requestId = upstream.headers.get("x-request-id");
    if (requestId) {
      outHeaders.set("x-request-id", requestId);
    }
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) {
      outHeaders.set("retry-after", retryAfter);
    }

    const setCookies =
      typeof upstream.headers.getSetCookie === "function"
        ? upstream.headers.getSetCookie()
        : [];
    for (const cookieStr of setCookies) {
      outHeaders.append("set-cookie", cookieStr);
    }

    return new NextResponse(responseBody, {
      status: upstream.status,
      headers: outHeaders,
    });
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "API_UNREACHABLE",
          message: "Control-plane backend service is temporarily unreachable.",
          request_id: request.headers.get("x-request-id") ?? "proxy-error",
          details: null,
        },
      },
      { status: 502 },
    );
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const PATCH = proxyRequest;
export const DELETE = proxyRequest;
