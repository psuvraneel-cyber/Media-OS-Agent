"""Hardened outbound HTTP client for research source fetches.

Guarantees per request:
* every hop validated by `validate_url` (https, exact-host allowlist, public resolved addresses);
* redirects followed manually (≤ policy.max_redirects), Authorization dropped off `auth_hosts`;
* connect/read/pool timeouts; streamed body with a hard byte cap;
* conditional GET (`If-None-Match` / `If-Modified-Since`) → `not_modified` on 304;
* per-source minimum-interval rate limiting before every attempt;
* bounded in-run retries for transient failures, honouring `Retry-After` up to a cap.

Secrets: the optional bearer token is held in memory only and never logged, stored, or echoed in
errors. Response bodies are returned as bytes for the caller to parse as untrusted data.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from media_os_api.research.http.errors import (
    PermanentFetchError,
    ResponseTooLargeError,
    RetryableFetchError,
    UnsafeUrlError,
)
from media_os_api.research.http.policy import DEFAULT_USER_AGENT, HostPolicy
from media_os_api.research.http.ratelimit import RateLimiter
from media_os_api.research.http.retry import RetryPolicy, backoff_delay, classify_status
from media_os_api.research.http.ssrf import (
    Resolver,
    is_public_address,
    system_resolver,
    validate_url,
)

logger = logging.getLogger("media_os_api.research.http")

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RATE_LIMIT_CODES = frozenset({"HTTP_429", "HTTP_403_RATE_LIMITED"})


@dataclass(frozen=True)
class HttpResult:
    url: str
    status_code: int
    body: bytes
    content_type: str | None
    etag: str | None
    last_modified: str | None
    not_modified: bool
    attempts: int
    headers: Mapping[str, str]


class SafeHttpClient:
    def __init__(
        self,
        *,
        source_code: str,
        policy: HostPolicy,
        rate_limiter: RateLimiter,
        min_interval_ms: int,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = system_resolver,
        retry_policy: RetryPolicy | None = None,
        bearer_token: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str | None = "Bearer",
        user_agent: str = DEFAULT_USER_AGENT,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.source_code = source_code
        self.policy = policy
        self._limiter = rate_limiter
        self._min_interval_ms = int(min_interval_ms)
        self._resolver = resolver
        self._retry = retry_policy or RetryPolicy()
        self._token = bearer_token.strip() if bearer_token and bearer_token.strip() else None
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self._sleep = sleep
        self._now = now
        self._user_agent = user_agent
        self.request_count = 0
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            trust_env=False,  # ignore ambient proxy env vars; no unintended egress paths
            timeout=httpx.Timeout(
                connect=policy.connect_timeout_seconds,
                read=policy.read_timeout_seconds,
                write=policy.read_timeout_seconds,
                pool=policy.pool_timeout_seconds,
            ),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        accept: str | None = None,
    ) -> HttpResult:
        """GET with retries; raises RetryableFetchError / PermanentFetchError subclasses."""
        return self._with_retries(
            lambda attempt: self._get_once(
                url,
                params=params,
                etag=etag,
                last_modified=last_modified,
                accept=accept,
                attempt=attempt,
            )
        )

    def post_json(
        self, url: str, payload: Mapping[str, Any], *, accept: str = "application/json"
    ) -> HttpResult:
        """POST a JSON body (no redirects followed) with the same retry/limit/SSRF guarantees."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self._with_retries(
            lambda attempt: self._get_once(
                url,
                params=None,
                etag=None,
                last_modified=None,
                accept=accept,
                attempt=attempt,
                method="POST",
                content=body,
            )
        )

    def _with_retries(self, send: Callable[[int], HttpResult]) -> HttpResult:
        attempt = 0
        while True:
            attempt += 1
            try:
                self._limiter.acquire(self.source_code, self._min_interval_ms)
                return send(attempt)
            except RetryableFetchError as exc:
                if exc.code in {"RATE_LIMITER_UNAVAILABLE", "RATE_LIMIT_WAIT_EXCEEDED"}:
                    raise
                if not self._retry.retry_on_rate_limit and exc.code in _RATE_LIMIT_CODES:
                    raise
                if attempt >= self._retry.max_attempts:
                    raise
                if exc.retry_after is not None:
                    wait = exc.retry_after.total_seconds()
                    if wait > self._retry.max_in_run_retry_after_seconds:
                        raise  # long server-requested wait → source-level backoff instead
                else:
                    wait = backoff_delay(attempt, self._retry)
                logger.info(
                    "research_fetch_retry",
                    extra={
                        "source": self.source_code,
                        "attempt": attempt,
                        "error_code": exc.code,
                        "wait_seconds": round(wait, 2),
                    },
                )
                self._sleep(wait)

    def _headers_for(
        self, host: str, *, etag: str | None, last_modified: str | None, accept: str | None
    ) -> dict[str, str]:
        headers = {"User-Agent": self._user_agent, "Accept": accept or "*/*"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        if self._token and host in self.policy.auth_hosts:
            value = f"{self._auth_scheme} {self._token}" if self._auth_scheme else self._token
            headers[self._auth_header] = value
        return headers

    def _check_peer(self, response: httpx.Response, host: str) -> None:
        """DNS-rebinding defence: verify the connected peer when the transport exposes it."""
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            return
        try:
            peer = stream.get_extra_info("server_addr")
        except Exception:  # noqa: BLE001 - informational only; absence is not an error
            return
        if peer and not is_public_address(str(peer[0])):
            raise UnsafeUrlError(f"Connected peer for '{host}' is not a public address.")

    def _get_once(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        etag: str | None,
        last_modified: str | None,
        accept: str | None,
        attempt: int,
        method: str = "GET",
        content: bytes | None = None,
    ) -> HttpResult:
        current = httpx.URL(url, params=dict(params) if params else None)
        for _hop in range(self.policy.max_redirects + 1):
            safe_url = validate_url(current, self.policy, self._resolver)
            host = (safe_url.host or "").lower()
            headers = self._headers_for(host, etag=etag, last_modified=last_modified, accept=accept)
            if content is not None:
                headers["Content-Type"] = "application/json"
            request = self._client.build_request(method, safe_url, headers=headers, content=content)
            self.request_count += 1
            try:
                response = self._client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise RetryableFetchError(f"Timeout fetching {host}.", code="HTTP_TIMEOUT") from exc
            except (httpx.TransportError, OSError) as exc:
                raise RetryableFetchError(
                    f"Network error fetching {host}: {type(exc).__name__}.", code="HTTP_NETWORK"
                ) from exc

            try:
                self._check_peer(response, host)
                status = response.status_code
                if status in _REDIRECT_STATUSES and method != "GET":
                    raise PermanentFetchError(
                        f"Refusing to follow a redirect for {method}.", code="BAD_REDIRECT"
                    )
                if status in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise PermanentFetchError(
                            "Redirect without Location header.", code="BAD_REDIRECT"
                        )
                    current = safe_url.join(location)
                    continue
                if status == 304:
                    return HttpResult(
                        url=str(safe_url),
                        status_code=304,
                        body=b"",
                        content_type=None,
                        etag=response.headers.get("etag") or etag,
                        last_modified=response.headers.get("last-modified") or last_modified,
                        not_modified=True,
                        attempts=attempt,
                        headers=dict(response.headers),
                    )
                if not 200 <= status <= 299:
                    raise classify_status(status, response.headers, now=self._now())
                body = self._read_capped(response, host)
                return HttpResult(
                    url=str(safe_url),
                    status_code=status,
                    body=body,
                    content_type=response.headers.get("content-type"),
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                    not_modified=False,
                    attempts=attempt,
                    headers=dict(response.headers),
                )
            finally:
                response.close()
        raise PermanentFetchError(
            f"Too many redirects (> {self.policy.max_redirects}).", code="TOO_MANY_REDIRECTS"
        )

    def _read_capped(self, response: httpx.Response, host: str) -> bytes:
        cap = self.policy.max_response_bytes
        declared = response.headers.get("content-length", "").strip()
        if declared.isdigit() and int(declared) > cap:
            raise ResponseTooLargeError(f"Response from {host} declares {declared} bytes > {cap}.")
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > cap:
                    raise ResponseTooLargeError(f"Response from {host} exceeded {cap} bytes.")
                chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise RetryableFetchError(f"Read timeout from {host}.", code="HTTP_TIMEOUT") from exc
        except httpx.TransportError as exc:
            raise RetryableFetchError(
                f"Connection dropped reading {host}.", code="HTTP_NETWORK"
            ) from exc
        return b"".join(chunks)
