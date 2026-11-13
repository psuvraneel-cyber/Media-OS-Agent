"""Unit tests for the research HTTP layer: SSRF, client behaviour, retry classification, limiter.

All traffic goes through `httpx.MockTransport` and a fake resolver; nothing leaves the host.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from media_os_api.jobs.errors import PermanentTaskError, RetryableTaskError
from media_os_api.research.http import (
    DEFAULT_USER_AGENT,
    HostPolicy,
    InMemoryMinIntervalLimiter,
    PermanentFetchError,
    RedisMinIntervalLimiter,
    ResponseTooLargeError,
    RetryableFetchError,
    RetryPolicy,
    SafeHttpClient,
    UnsafeUrlError,
    classify_status,
    is_public_address,
    parse_retry_after,
    validate_url,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
PUBLIC_IP = "140.82.112.5"
POLICY = HostPolicy(
    allowed_hosts=frozenset({"api.example.org", "cdn.example.org"}),
    auth_hosts=frozenset({"api.example.org"}),
    max_response_bytes=4096,
)

DNS: dict[str, list[str]] = {
    "api.example.org": [PUBLIC_IP],
    "cdn.example.org": ["151.101.1.1", "2a04:4e42::1"],
    "evil.example.org": [PUBLIC_IP],
}


def fake_resolver(host: str, _port: int) -> list[str]:
    return DNS.get(host, [])


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _CountingLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def acquire(self, source_code: str, min_interval_ms: int) -> None:
        self.calls.append((source_code, min_interval_ms))


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token: str | None = None,
    limiter: Any = None,
    sleeps: _Sleeps | None = None,
    policy: HostPolicy = POLICY,
    resolver: Callable[[str, int], list[str]] = fake_resolver,
) -> SafeHttpClient:
    return SafeHttpClient(
        source_code="TESTSRC",
        policy=policy,
        rate_limiter=limiter or _CountingLimiter(),
        min_interval_ms=500,
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.5),
        bearer_token=token,
        sleep=sleeps or _Sleeps(),
        now=lambda: NOW,
    )


# --------------------------------------------------------------------------- SSRF / URL policy


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.org/x",  # not https
        "https://user:pw@api.example.org/x",  # userinfo
        "https://api.example.org:8443/x",  # non-default port
        "https://140.82.112.5/x",  # IP literal
        "https://[::1]/x",
        "https://other.example.org/x",  # not allowlisted
        "https://API.example.org.evil.com/x",
        "ftp://api.example.org/x",
        "not a url",
    ],
)
def test_validate_url_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(url, POLICY, fake_resolver)


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.1.2.3"],
        ["192.168.0.10"],
        ["172.16.5.5"],
        ["169.254.169.254"],  # cloud metadata
        ["100.64.0.1"],  # CGNAT
        ["0.0.0.0"],  # noqa: S104 - asserting it is rejected, not binding
        ["::1"],
        ["fd00::1"],  # ULA
        ["fe80::1"],
        ["::ffff:127.0.0.1"],  # IPv4-mapped loopback
        ["224.0.0.1"],
        [PUBLIC_IP, "10.0.0.1"],  # any private address poisons the host
    ],
)
def test_validate_url_rejects_non_public_resolution(addresses: list[str]) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url("https://api.example.org/x", POLICY, lambda _h, _p: addresses)


def test_validate_url_accepts_public_dual_stack_and_dns_failure_is_retryable() -> None:
    assert validate_url("https://cdn.example.org/a?b=1", POLICY, fake_resolver).host == (
        "cdn.example.org"
    )
    with pytest.raises(RetryableFetchError) as excinfo:
        validate_url("https://api.example.org/x", POLICY, lambda _h, _p: [])
    assert excinfo.value.code == "DNS_NO_ADDRESSES"


def test_is_public_address() -> None:
    assert is_public_address("8.8.8.8")
    assert is_public_address("2606:4700:4700::1111")
    assert not is_public_address("garbage")
    assert not is_public_address("::ffff:10.0.0.1")


def test_host_policy_validation() -> None:
    with pytest.raises(ValueError):
        HostPolicy(allowed_hosts=frozenset())
    with pytest.raises(ValueError):
        HostPolicy(allowed_hosts=frozenset({"*.example.org"}))
    with pytest.raises(ValueError):
        HostPolicy(allowed_hosts=frozenset({"a.org"}), auth_hosts=frozenset({"b.org"}))


# --------------------------------------------------------------------------- client behaviour


def test_success_sends_safe_headers_and_rate_limits_each_attempt() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"etag": '"v1"'})

    limiter = _CountingLimiter()
    with make_client(handler, limiter=limiter) as client:
        result = client.get("https://api.example.org/items", params={"limit": 3})
    assert result.status_code == 200 and result.etag == '"v1"' and not result.not_modified
    req = seen[0]
    assert req.url.params["limit"] == "3"
    assert req.headers["user-agent"] == DEFAULT_USER_AGENT
    assert "@" not in DEFAULT_USER_AGENT  # no personal contact data
    assert "authorization" not in req.headers
    assert limiter.calls == [("TESTSRC", 500)]


def test_conditional_request_and_304() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v1"'
        assert request.headers["if-modified-since"] == "Wed, 12 Nov 2026 10:00:00 GMT"
        return httpx.Response(304)

    with make_client(handler) as client:
        result = client.get(
            "https://api.example.org/feed",
            etag='"v1"',
            last_modified="Wed, 12 Nov 2026 10:00:00 GMT",
        )
    assert result.not_modified and result.body == b""
    assert result.etag == '"v1"'  # validators carried forward when the server omits them


def test_bearer_token_only_sent_to_auth_hosts_and_dropped_on_redirect() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.example.org":
            return httpx.Response(302, headers={"location": "https://cdn.example.org/blob"})
        return httpx.Response(200, content=b"data")

    with make_client(handler, token="test-token-value") as client:
        result = client.get("https://api.example.org/start")
    assert result.body == b"data" and result.url.startswith("https://cdn.example.org/")
    assert seen[0].headers["authorization"] == "Bearer test-token-value"
    assert "authorization" not in seen[1].headers


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.org/x",  # off-allowlist
        "http://cdn.example.org/x",  # downgrade to http
        "https://169.254.169.254/latest/meta-data",  # metadata IP literal
    ],
)
def test_redirect_targets_are_revalidated(location: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": location})

    with make_client(handler) as client, pytest.raises(UnsafeUrlError):
        client.get("https://api.example.org/start")


def test_redirect_to_host_resolving_privately_is_refused() -> None:
    def resolver(host: str, port: int) -> list[str]:
        return ["10.0.0.7"] if host == "cdn.example.org" else fake_resolver(host, port)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://cdn.example.org/x"})

    with make_client(handler, resolver=resolver) as client, pytest.raises(UnsafeUrlError):
        client.get("https://api.example.org/start")


def test_redirect_loop_is_bounded() -> None:
    hops: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        return httpx.Response(302, headers={"location": "/again"})

    with make_client(handler) as client, pytest.raises(PermanentFetchError) as excinfo:
        client.get("https://api.example.org/start")
    assert excinfo.value.code == "TOO_MANY_REDIRECTS"
    assert len(hops) == POLICY.max_redirects + 1


def test_body_cap_enforced_with_and_without_content_length() -> None:
    big = b"x" * (POLICY.max_response_bytes + 1)

    def declared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big)

    def streamed(request: httpx.Request) -> httpx.Response:
        def gen() -> Any:
            for _ in range(10):
                yield b"y" * 1000

        return httpx.Response(200, content=gen())

    for handler in (declared, streamed):
        with make_client(handler) as client, pytest.raises(ResponseTooLargeError):
            client.get("https://api.example.org/big")


def test_transient_errors_retry_with_backoff_then_succeed() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("slow", request=request)
        if calls["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    sleeps = _Sleeps()
    limiter = _CountingLimiter()
    with make_client(handler, sleeps=sleeps, limiter=limiter) as client:
        result = client.get("https://api.example.org/flaky")
    assert result.body == b"ok" and result.attempts == 3
    assert len(sleeps.calls) == 2 and all(0 <= s <= 1.0 for s in sleeps.calls)
    assert len(limiter.calls) == 3  # limiter consulted before every attempt


def test_retry_after_is_honoured_within_cap_and_raised_beyond_it() -> None:
    def short(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "7"})

    sleeps = _Sleeps()
    with make_client(short, sleeps=sleeps) as client, pytest.raises(RetryableFetchError):
        client.get("https://api.example.org/limited")
    assert sleeps.calls == [7.0, 7.0]  # 3 attempts, 2 waits, exact server value

    def long(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"retry-after": "3600"})

    sleeps = _Sleeps()
    with make_client(long, sleeps=sleeps) as client, pytest.raises(RetryableFetchError) as exc:
        client.get("https://api.example.org/limited")
    assert sleeps.calls == []  # not slept in-process
    assert exc.value.retry_after == timedelta(seconds=3600)


def test_permanent_status_is_not_retried_and_retries_are_bounded() -> None:
    calls = {"n": 0}

    def not_found(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with make_client(not_found) as client, pytest.raises(PermanentFetchError) as excinfo:
        client.get("https://api.example.org/missing")
    assert excinfo.value.code == "HTTP_404" and calls["n"] == 1
    assert isinstance(excinfo.value, PermanentTaskError)

    calls["n"] = 0

    def always_down(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("t", request=request)

    with make_client(always_down) as client, pytest.raises(RetryableFetchError) as exc2:
        client.get("https://api.example.org/down")
    assert exc2.value.code == "HTTP_TIMEOUT" and calls["n"] == 3
    assert isinstance(exc2.value, RetryableTaskError)


def test_rate_limiter_unavailability_fails_closed_without_request() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200)

    class _Down:
        def acquire(self, source_code: str, min_interval_ms: int) -> None:
            raise RetryableFetchError("down", code="RATE_LIMITER_UNAVAILABLE")

    with make_client(handler, limiter=_Down()) as client, pytest.raises(RetryableFetchError):
        client.get("https://api.example.org/x")
    assert calls["n"] == 0


# --------------------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("status", "headers", "retryable", "code"),
    [
        (500, {}, True, "HTTP_500"),
        (502, {}, True, "HTTP_502"),
        (503, {}, True, "HTTP_503"),
        (504, {}, True, "HTTP_504"),
        (408, {}, True, "HTTP_408"),
        (429, {}, True, "HTTP_429"),
        (400, {}, False, "HTTP_400"),
        (401, {}, False, "HTTP_401"),
        (403, {}, False, "HTTP_403"),
        (404, {}, False, "HTTP_404"),
        (410, {}, False, "HTTP_410"),
        (403, {"X-RateLimit-Remaining": "0"}, True, "HTTP_403_RATE_LIMITED"),
    ],
)
def test_classify_status(status: int, headers: dict[str, str], retryable: bool, code: str) -> None:
    err = classify_status(status, headers, now=NOW)
    assert isinstance(err, RetryableFetchError) is retryable
    assert err.code == code


def test_github_quota_reset_becomes_retry_after() -> None:
    reset = int((NOW + timedelta(minutes=20)).timestamp())
    err = classify_status(
        403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)}, now=NOW
    )
    assert isinstance(err, RetryableFetchError)
    assert err.retry_after == timedelta(minutes=20)


def test_parse_retry_after_forms() -> None:
    assert parse_retry_after("120", now=NOW) == timedelta(seconds=120)
    assert parse_retry_after("Thu, 13 Nov 2026 12:05:00 GMT", now=NOW) == timedelta(minutes=5)
    assert parse_retry_after("Thu, 13 Nov 2026 11:00:00 GMT", now=NOW) == timedelta(0)
    assert parse_retry_after("soon", now=NOW) is None
    assert parse_retry_after(None, now=NOW) is None


# --------------------------------------------------------------------------- rate limiters


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def test_in_memory_limiter_spaces_requests_per_source() -> None:
    clock = _Clock()
    limiter = InMemoryMinIntervalLimiter(sleep=clock.sleep, monotonic=clock.monotonic)
    limiter.acquire("ARXIV", 3100)
    limiter.acquire("ARXIV", 3100)
    limiter.acquire("HACKERNEWS", 250)  # independent key: no wait
    assert clock.sleeps == [pytest.approx(3.1)]


class _FakeRedis:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.expiry: dict[str, float] = {}

    def set(self, name: str, value: str, *, nx: bool, px: int) -> bool:
        exp = self.expiry.get(name)
        if exp is not None and exp > self.clock.t:
            return False
        self.expiry[name] = self.clock.t + px / 1000.0
        return True

    def pttl(self, name: str) -> int:
        exp = self.expiry.get(name)
        if exp is None or exp <= self.clock.t:
            return -2
        return int((exp - self.clock.t) * 1000)


def test_redis_limiter_enforces_spacing_and_bounded_wait() -> None:
    clock = _Clock()
    limiter = RedisMinIntervalLimiter(
        _FakeRedis(clock), sleep=clock.sleep, monotonic=clock.monotonic, max_wait_seconds=5
    )
    limiter.acquire("ARXIV", 3100)
    limiter.acquire("ARXIV", 3100)
    assert sum(clock.sleeps) == pytest.approx(3.1, abs=0.02)

    slow = RedisMinIntervalLimiter(
        _FakeRedis(clock), sleep=clock.sleep, monotonic=clock.monotonic, max_wait_seconds=1
    )
    slow.acquire("GITHUB", 6500)
    with pytest.raises(RetryableFetchError) as excinfo:
        slow.acquire("GITHUB", 6500)
    assert excinfo.value.code == "RATE_LIMIT_WAIT_EXCEEDED"


def test_redis_limiter_fails_closed_on_outage() -> None:
    class _Broken:
        def set(self, name: str, value: str, *, nx: bool, px: int) -> bool:
            raise RedisConnectionError("refused")

        def pttl(self, name: str) -> int:
            raise RedisConnectionError("refused")

    with pytest.raises(RetryableFetchError) as excinfo:
        RedisMinIntervalLimiter(_Broken()).acquire("HACKERNEWS", 250)
    assert excinfo.value.code == "RATE_LIMITER_UNAVAILABLE"
