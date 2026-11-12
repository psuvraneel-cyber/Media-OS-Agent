import { NextResponse } from "next/server";

export async function GET(): Promise<NextResponse> {
  return NextResponse.json(
    {
      status: "ok",
      service: "media-os-web",
      phase: "phase-4-persistence",
      timestamp_utc: new Date().toISOString(),
    },
    { status: 200 },
  );
}
