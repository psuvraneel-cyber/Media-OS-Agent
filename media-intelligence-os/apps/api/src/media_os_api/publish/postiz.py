"""Minimal Postiz Public API client (v2.25.3) for scheduling carousels.

Postiz runs locally (loopback / host.docker.internal), so this client talks only to the configured
base URL and never follows redirects. Auth is the raw API key in `Authorization` (Postiz rejects a
`Bearer` prefix). The key is read from the environment and never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

INSTAGRAM_CAPTION_MAX = 2200


class PostizError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class Integration:
    id: str
    provider: str  # instagram | facebook | ...
    name: str
    disabled: bool


def _check_base(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in (
        "localhost",
        "127.0.0.1",
        "host.docker.internal",
    ):
        raise ValueError("POSTIZ_BASE_URL must point at the local Postiz instance.")
    return url.rstrip("/")


class PostizClient:
    def __init__(
        self, *, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not api_key.strip():
            raise ValueError("POSTIZ_API_KEY is not set.")
        self._http = httpx.Client(
            base_url=f"{_check_base(base_url)}/api/public/v1",
            headers={"Authorization": api_key.strip(), "User-Agent": "media-intelligence-os"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PostizError("Postiz unreachable.", code="POSTIZ_NETWORK", retryable=True) from exc
        if resp.status_code == 401:
            raise PostizError("Postiz rejected the API key.", code="POSTIZ_AUTH", retryable=False)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise PostizError(
                f"Postiz temporarily failed (HTTP {resp.status_code}).",
                code=f"POSTIZ_HTTP_{resp.status_code}",
                retryable=True,
            )
        if resp.status_code >= 400:
            detail = resp.text[:300].replace("\n", " ")
            raise PostizError(
                f"Postiz refused the request (HTTP {resp.status_code}): {detail}",
                code=f"POSTIZ_HTTP_{resp.status_code}",
                retryable=False,
            )
        return resp.json() if resp.content else None

    def integrations(self) -> list[Integration]:
        data = self._call("GET", "/integrations")
        items = data if isinstance(data, list) else (data or {}).get("integrations", [])
        return [
            Integration(
                id=str(i.get("id")),
                provider=str(i.get("identifier") or i.get("providerIdentifier") or ""),
                name=str(i.get("name") or ""),
                disabled=bool(i.get("disabled")),
            )
            for i in items
            if isinstance(i, dict) and i.get("id")
        ]

    def schedule_carousel(
        self,
        *,
        when: datetime,
        caption: str,
        image_urls: list[str],
        targets: list[Integration],
        image_prefix: str,
    ) -> dict[str, str]:
        """Create one scheduled post per target; returns {integration_id: postiz_post_id}."""
        if when.tzinfo is None:
            raise ValueError("Schedule time must be timezone-aware.")
        if len(caption) > INSTAGRAM_CAPTION_MAX:
            raise ValueError("Caption longer than Instagram's 2200 characters.")
        images = [{"id": f"{image_prefix}-{i}", "path": url} for i, url in enumerate(image_urls, 1)]
        posts = []
        for t in targets:
            settings: dict[str, Any] = {"__type": t.provider}
            if t.provider in ("instagram", "instagram-standalone"):
                settings.update({"post_type": "post", "collaborators": []})
            if t.provider == "facebook":
                settings.update({"post_type": "post"})
            posts.append(
                {
                    "integration": {"id": t.id},
                    "value": [{"content": caption, "image": images}],
                    "settings": settings,
                }
            )
        body = {
            "type": "schedule",
            "shortLink": False,
            "date": when.isoformat(),
            "tags": [],
            "posts": posts,
        }
        created = self._call("POST", "/posts", json=body) or []
        return {
            str(c.get("integration")): str(c.get("postId"))
            for c in created
            if isinstance(c, dict) and c.get("postId") and c.get("integration")
        }

    def delete_post(self, post_id: str) -> None:
        self._call("DELETE", f"/posts/{post_id}")

    def post_analytics(self, post_id: str) -> list[dict[str, Any]] | None:
        """Lifetime metrics for one published post (`GET /analytics/post/{id}`), read-only.

        Returns the provider's list of `{label, data: [{total, date}]}`; an empty list when the
        post has no release yet or the provider exposes no post analytics; None when Postiz marks
        the post `missing` on the platform.
        """
        data = self._call("GET", f"/analytics/post/{post_id}", params={"date": 7})
        if isinstance(data, dict) and data.get("missing"):
            return None
        return [m for m in data or [] if isinstance(m, dict)] if isinstance(data, list) else []

    def account_analytics(self, integration_id: str, days: int) -> list[dict[str, Any]]:
        """Account-wide metrics for one channel over the last `days` days
        (`GET /analytics/{integration}`), read-only. Covers every post on the account."""
        data = self._call("GET", f"/analytics/{integration_id}", params={"date": days})
        return [m for m in data or [] if isinstance(m, dict)] if isinstance(data, list) else []

    def posts_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        data = self._call(
            "GET", "/posts", params={"startDate": start.isoformat(), "endDate": end.isoformat()}
        )
        posts = (data or {}).get("posts", []) if isinstance(data, dict) else data
        return [p for p in posts or [] if isinstance(p, dict)]
