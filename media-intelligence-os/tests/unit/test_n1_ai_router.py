"""N1 free-tier AI adapter: provider adapters, quota tracking and seamless cross-provider failover.

All traffic goes through `httpx.MockTransport` and a fake resolver; nothing leaves the host.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from redis.exceptions import ConnectionError as RedisConnectionError

from media_os_api.ai.config import build_router, providers_from_env
from media_os_api.ai.errors import AIUnavailable, AllProvidersExhausted, InvalidModelOutput
from media_os_api.ai.providers import (
    CloudflareProvider,
    GeminiProvider,
    GroqProvider,
    OpenRouterProvider,
)
from media_os_api.ai.quota import (
    InMemoryQuotaStore,
    QuotaLimits,
    QuotaTracker,
    RedisQuotaStore,
    next_daily_reset,
)
from media_os_api.ai.router import extract_json
from media_os_api.ai.types import GenerationRequest, ModelRoute, TaskClass
from media_os_api.jobs.errors import RetryableTaskError
from media_os_api.research.http.ratelimit import InMemoryMinIntervalLimiter

pytestmark = pytest.mark.unit

ACCOUNT = "0123456789abcdef0123456789abcdef"
SECRETS = {
    "GEMINI_API_KEY": "gm-test-key-123",
    "GROQ_API_KEY": "gq-test-key-456",
    "OPENROUTER_API_KEY": "or-test-key-789",
    "CLOUDFLARE_API_TOKEN": "cf-test-token-000",
    "CLOUDFLARE_ACCOUNT_ID": ACCOUNT,
}
IP = "142.250.1.1"


def resolver(_host: str, _port: int) -> list[str]:
    return [IP]


def gemini_ok(text: str = "নমস্কার") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4},
        },
    )


def openai_ok(text: str = "hello") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
        },
    )


def cloudflare_ok(text: str = "hi") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "result": {"response": text, "usage": {"prompt_tokens": 5, "completion_tokens": 1}},
        },
    )


Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    def __init__(self, handlers: dict[str, Handler]) -> None:
        self.handlers = handlers
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handlers[request.url.host](request)

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


def make_router(
    handlers: dict[str, Handler],
    *,
    routes: dict[str, list[list[str]]] | None = None,
    limits: dict[str, dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[Any, Recorder, list[float], list[tuple[str, str]]]:
    environ = dict(SECRETS if env is None else env)
    if routes is not None:
        environ["AI_ROUTES"] = json.dumps(routes)
    if limits is not None:
        environ["AI_QUOTA_LIMITS"] = json.dumps(limits)
    rec = Recorder(handlers)
    sleeps: list[float] = []
    attempts: list[tuple[str, str]] = []
    router = build_router(
        quota_store=InMemoryQuotaStore(),
        environ=environ,
        rate_limiter=InMemoryMinIntervalLimiter(sleep=sleeps.append),
        transport=httpx.MockTransport(rec),
        resolver=resolver,
        on_attempt=lambda _req, a: attempts.append((a.provider, a.outcome)),
        sleep=sleeps.append,
    )
    return router, rec, sleeps, attempts


def req(task: TaskClass = TaskClass.ENGLISH_COPY, schema: type[BaseModel] | None = None) -> Any:
    return GenerationRequest(task=task, system="sys", prompt="prompt", output_schema=schema)


TWO_ROUTES = {"ENGLISH_COPY": [["gemini", "gemini-2.5-flash"], ["groq", "llama-3.3-70b-versatile"]]}


# ---------------------------------------------------------------- provider adapters


def test_gemini_payload_and_parse() -> None:
    p = GeminiProvider()
    assert p.endpoint("gemini-2.5-flash").endswith("/models/gemini-2.5-flash:generateContent")

    class Out(BaseModel):
        a: int

    body = p.payload("gemini-2.5-flash", req(schema=Out))
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    parsed = p.parse(gemini_ok().content)
    assert (parsed.text, parsed.input_tokens, parsed.output_tokens) == ("নমস্কার", 11, 4)


def test_gemini_blocked_and_bad_model() -> None:
    p = GeminiProvider()
    with pytest.raises(InvalidModelOutput) as exc:
        p.parse(json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode())
    assert exc.value.code == "BLOCKED"
    with pytest.raises(ValueError):
        p.endpoint("../../v1/other")


def test_openai_compatible_parse_and_json_mode() -> None:
    p = GroqProvider()

    class Out(BaseModel):
        a: int

    assert p.payload("llama-3.1-8b-instant", req(schema=Out))["response_format"] == {
        "type": "json_object"
    }
    assert p.parse(openai_ok("x").content).text == "x"
    with pytest.raises(InvalidModelOutput):
        p.parse(json.dumps({"error": {"message": "nope"}}).encode())


def test_openrouter_only_free_models() -> None:
    p = OpenRouterProvider()
    with pytest.raises(ValueError, match="free"):
        p.endpoint("openai/gpt-4o")
    assert p.endpoint("meta-llama/llama-3.3-70b-instruct:free").endswith("/chat/completions")
    listing = {"data": [{"id": "a/b:free"}, {"id": "a/paid"}]}
    assert p.parse_models(json.dumps(listing).encode()) == ["a/b:free"]


def test_cloudflare_account_validation_and_parse() -> None:
    with pytest.raises(ValueError):
        CloudflareProvider("not-an-id/../x")
    p = CloudflareProvider(ACCOUNT)
    assert p.endpoint("@cf/meta/llama-3.1-8b-instruct") == (
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/@cf/meta/llama-3.1-8b-instruct"
    )
    assert p.parse(cloudflare_ok("yo").content).text == "yo"
    with pytest.raises(InvalidModelOutput):
        p.parse(json.dumps({"success": False}).encode())
    chat_shape = {
        "success": True,
        "result": {
            "choices": [{"message": {"content": "নমস্কার"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    }
    parsed = p.parse(json.dumps(chat_shape).encode())
    assert (parsed.text, parsed.input_tokens, parsed.output_tokens) == ("নমস্কার", 3, 2)


def test_providers_from_env_skips_missing_credentials() -> None:
    assert sorted(providers_from_env(SECRETS)) == ["cloudflare", "gemini", "groq", "openrouter"]
    partial = {"GROQ_API_KEY": "x", "CLOUDFLARE_API_TOKEN": "y"}  # no account id
    assert sorted(providers_from_env(partial)) == ["groq"]
    placeholders = {"GEMINI_API_KEY": "REPLACE_WITH_GEMINI_API_KEY", "GROQ_API_KEY": " "}
    assert providers_from_env(placeholders) == {}


def test_extract_json_handles_fences_and_prose() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 2} hope this helps') == {"a": 2}
    with pytest.raises(InvalidModelOutput):
        extract_json("no json here")


# ---------------------------------------------------------------- credentials


def test_auth_headers_per_provider_and_never_in_records() -> None:
    routes = {
        "ENGLISH_COPY": [["gemini", "gemini-2.5-flash"]],
        "SIMPLE_FORMAT": [["groq", "llama-3.1-8b-instant"]],
        "REVIEW": [["cloudflare", "@cf/meta/llama-3.1-8b-instruct"]],
    }
    router, rec, _s, attempts = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: gemini_ok(),
            "api.groq.com": lambda r: openai_ok(),
            "api.cloudflare.com": lambda r: cloudflare_ok(),
        },
        routes=routes,
    )
    router.generate(req(TaskClass.ENGLISH_COPY))
    router.generate(req(TaskClass.SIMPLE_FORMAT))
    result = router.generate(req(TaskClass.REVIEW))
    g, q, c = rec.requests
    assert g.headers["x-goog-api-key"] == SECRETS["GEMINI_API_KEY"]
    assert "authorization" not in g.headers
    assert q.headers["authorization"] == f"Bearer {SECRETS['GROQ_API_KEY']}"
    assert c.headers["authorization"] == f"Bearer {SECRETS['CLOUDFLARE_API_TOKEN']}"
    for r in rec.requests:
        assert all(v not in str(r.url) for v in SECRETS.values() if v != ACCOUNT)
        assert not any(v in r.content.decode() for v in SECRETS.values())
    assert all(s not in repr(result.attempts) + repr(attempts) for s in SECRETS.values())


# ---------------------------------------------------------------- failover


def test_429_fails_over_to_next_provider_without_sleeping() -> None:
    router, rec, sleeps, attempts = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: httpx.Response(429),
            "api.groq.com": lambda r: openai_ok("from groq"),
        },
        routes=TWO_ROUTES,
    )
    result = router.generate(req())
    assert (result.provider, result.text) == ("groq", "from groq")
    assert rec.hosts() == ["generativelanguage.googleapis.com", "api.groq.com"]
    assert sleeps == []  # no in-process wait on a rate limit
    assert attempts == [("gemini", "QUOTA"), ("groq", "OK")]

    # The blocked route is skipped instantly on the next step (no request sent).
    router.generate(req())
    assert rec.hosts()[2:] == ["api.groq.com"]
    assert attempts[2] == ("gemini", "SKIPPED_QUOTA")


def test_short_retry_after_blocks_only_briefly() -> None:
    router, _rec, _s, attempts = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: httpx.Response(
                429, headers={"Retry-After": "20"}
            ),
            "api.groq.com": lambda r: openai_ok(),
        },
        routes=TWO_ROUTES,
    )
    router.generate(req())
    assert attempts[0] == ("gemini", "RATE_LIMITED")
    until = router._quota.blocked_until(ModelRoute("gemini", "gemini-2.5-flash"))
    assert until is not None
    assert (until - datetime.now(UTC)).total_seconds() <= 21


def test_all_routes_exhausted_is_retryable_with_resume_time() -> None:
    router, _rec, _s, _a = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: httpx.Response(429),
            "api.groq.com": lambda r: httpx.Response(429),
        },
        routes=TWO_ROUTES,
    )
    with pytest.raises(AllProvidersExhausted) as exc:
        router.generate(req())
    assert isinstance(exc.value, RetryableTaskError)
    assert exc.value.resume_at is not None and exc.value.resume_at > datetime.now(UTC)


def test_bad_key_pauses_provider_and_fails_over() -> None:
    router, rec, _s, attempts = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: httpx.Response(401),
            "api.groq.com": lambda r: openai_ok(),
        },
        routes={
            "ENGLISH_COPY": [
                ["gemini", "gemini-2.5-flash"],
                ["gemini", "gemini-2.5-flash-lite"],
                ["groq", "llama-3.3-70b-versatile"],
            ]
        },
    )
    assert router.generate(req()).provider == "groq"
    # The whole provider is paused, so the second Gemini model is not even tried.
    assert attempts == [("gemini", "AUTH"), ("gemini", "SKIPPED_QUOTA"), ("groq", "OK")]
    assert rec.hosts().count("generativelanguage.googleapis.com") == 1


def test_server_errors_trip_breaker_and_fail_over() -> None:
    router, rec, _s, attempts = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: httpx.Response(503),
            "api.groq.com": lambda r: openai_ok(),
        },
        routes=TWO_ROUTES,
    )
    for _ in range(3):
        assert router.generate(req()).provider == "groq"
    assert attempts.count(("gemini", "TRANSIENT")) == 3
    gemini_calls = rec.hosts().count("generativelanguage.googleapis.com")
    router.generate(req())
    assert rec.hosts().count("generativelanguage.googleapis.com") == gemini_calls  # breaker open


def test_invalid_output_gets_one_repair_on_same_route() -> None:
    class Hook(BaseModel):
        hook: str

    replies = iter([openai_ok("I think the hook is great"), openai_ok('{"hook": "দারুণ"}')])
    router, rec, _s, attempts = make_router(
        {"api.groq.com": lambda r: next(replies)},
        routes={"ENGLISH_COPY": [["groq", "llama-3.3-70b-versatile"]]},
    )
    result = router.generate(req(schema=Hook))
    assert isinstance(result.data, Hook) and result.data.hook == "দারুণ"
    assert attempts == [("groq", "INVALID_OUTPUT"), ("groq", "OK")]
    assert "previous reply could not be parsed" in rec.requests[1].content.decode()


def test_invalid_output_twice_moves_to_next_provider() -> None:
    class Hook(BaseModel):
        hook: str

    router, _rec, _s, attempts = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: gemini_ok("not json"),
            "api.groq.com": lambda r: openai_ok('{"hook": "ok"}'),
        },
        routes=TWO_ROUTES,
    )
    assert router.generate(req(schema=Hook)).provider == "groq"
    assert attempts == [("gemini", "INVALID_OUTPUT"), ("gemini", "INVALID_OUTPUT"), ("groq", "OK")]


def test_self_imposed_daily_cap_switches_before_provider_refuses() -> None:
    router, rec, _s, attempts = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: gemini_ok(),
            "api.groq.com": lambda r: openai_ok(),
        },
        routes=TWO_ROUTES,
        limits={"gemini:gemini-2.5-flash": {"rpd": 2}},
    )
    providers = [router.generate(req()).provider for _ in range(4)]
    assert providers == ["gemini", "gemini", "groq", "groq"]
    assert rec.hosts().count("generativelanguage.googleapis.com") == 2


def test_no_configured_provider_is_unavailable_not_exhausted() -> None:
    router, _rec, _s, _a = make_router({}, routes=TWO_ROUTES, env={})
    with pytest.raises(AIUnavailable):
        router.generate(req())


# ---------------------------------------------------------------- quota store


def test_daily_reset_uses_provider_timezone() -> None:
    now = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)  # 01:30 in Los Angeles
    assert next_daily_reset(now, "America/Los_Angeles") == datetime(2026, 11, 15, 8, 0, tzinfo=UTC)
    assert next_daily_reset(now, "UTC") == datetime(2026, 11, 15, 0, 0, tzinfo=UTC)


def test_in_memory_rpm_cap() -> None:
    tracker = QuotaTracker(InMemoryQuotaStore(), {"groq": QuotaLimits(rpm=2)})
    route = ModelRoute("groq", "m")
    assert tracker.reserve(route) and tracker.reserve(route)
    assert not tracker.reserve(route)
    assert tracker.blocked_until(route) is not None


def test_redis_store_fails_closed() -> None:
    class Broken:
        def get(self, _key: str) -> Any:
            raise RedisConnectionError("down")

    tracker = QuotaTracker(RedisQuotaStore(Broken()), {})
    with pytest.raises(AIUnavailable):
        tracker.reserve(ModelRoute("groq", "m"))


def test_default_routes_are_valid_for_their_providers() -> None:
    from media_os_api.ai.config import DEFAULT_ROUTES

    providers = providers_from_env(SECRETS)
    for task in TaskClass:
        routes = DEFAULT_ROUTES[task]
        assert len({r.provider for r in routes}) >= 2, f"{task} needs cross-provider failover"
        for route in routes:
            providers[route.provider].endpoint(route.model)  # raises on an invalid/paid id


# ---------------------------------------------------------------- truncation / thinking budgets


def test_output_token_floors_for_thinking_models() -> None:
    small = GenerationRequest(task=TaskClass.REVIEW, system="s", prompt="p", max_output_tokens=16)
    assert (
        GeminiProvider().payload("gemini-3.5-flash", small)["generationConfig"]["maxOutputTokens"]
        == 1024
    )
    groq = GroqProvider()
    assert groq.payload("openai/gpt-oss-120b", small)["max_tokens"] == 512
    assert groq.payload("qwen/qwen3.8-27b", small)["max_tokens"] == 16


def test_truncated_replies_are_detected_per_provider() -> None:
    gemini_cut = {
        "candidates": [
            {
                "content": {"parts": [{"text": "thinking...", "thought": True}, {"text": ":**"}]},
                "finishReason": "MAX_TOKENS",
            }
        ]
    }
    openai_cut = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    cf_cut = {"success": True, "result": openai_cut}
    for provider, doc in (
        (GeminiProvider(), gemini_cut),
        (GroqProvider(), openai_cut),
        (CloudflareProvider(ACCOUNT), cf_cut),
    ):
        with pytest.raises(InvalidModelOutput) as exc:
            provider.parse(json.dumps(doc).encode())
        assert exc.value.code == "TRUNCATED"


def test_gemini_thought_parts_are_not_returned_as_text() -> None:
    doc = {
        "candidates": [
            {
                "content": {"parts": [{"text": "plan", "thought": True}, {"text": "উত্তর"}]},
                "finishReason": "STOP",
            }
        ]
    }
    assert GeminiProvider().parse(json.dumps(doc).encode()).text == "উত্তর"


def test_truncation_moves_to_next_provider_without_repair_attempt() -> None:
    cut = httpx.Response(
        200, json={"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}
    )
    router, rec, _s, attempts = make_router(
        {
            "generativelanguage.googleapis.com": lambda r: cut,
            "api.groq.com": lambda r: openai_ok("ঠিক আছে"),
        },
        routes=TWO_ROUTES,
    )
    assert router.generate(req()).provider == "groq"
    assert attempts == [("gemini", "TRUNCATED"), ("groq", "OK")]
    assert rec.hosts().count("generativelanguage.googleapis.com") == 1  # no wasted retry
