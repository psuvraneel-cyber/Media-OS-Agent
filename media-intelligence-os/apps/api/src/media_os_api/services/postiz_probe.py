"""Safe read-only HTTP reachability check for standalone `postiz-runtime`."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from media_os_api.schemas.system import PostizReachabilitySummary


async def probe_postiz_reachability(
    base_url: str,
    *,
    timeout_seconds: float = 2.0,
) -> PostizReachabilitySummary:
    """Check whether `postiz-runtime` responds on its public readiness route without keys."""
    checked_at = datetime.now(UTC).isoformat()
    probe_url = f"{base_url.rstrip('/')}/api/auth/can-register"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            response = await client.get(probe_url)
    except httpx.TimeoutException:
        return PostizReachabilitySummary(
            status="unreachable",
            checked_at_utc=checked_at,
            detail="Timed out reaching standalone Postiz HTTP endpoint.",
        )
    except httpx.RequestError:
        return PostizReachabilitySummary(
            status="unreachable",
            checked_at_utc=checked_at,
            detail="Standalone Postiz HTTP endpoint did not respond cleanly.",
        )

    if 200 <= response.status_code < 400:
        return PostizReachabilitySummary(
            status="reachable",
            checked_at_utc=checked_at,
            detail=f"Standalone Postiz HTTP gateway responded with status {response.status_code}.",
        )
    return PostizReachabilitySummary(
        status="error",
        checked_at_utc=checked_at,
        detail=f"Standalone Postiz HTTP gateway returned unexpected status {response.status_code}.",
    )
