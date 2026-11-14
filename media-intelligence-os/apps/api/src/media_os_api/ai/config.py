"""Default routes, conservative free-tier caps, and the environment-based router builder.

Model IDs and limits are configuration, not code facts: free tiers and model names change.
The defaults below are conservative starting points and must be confirmed against each
provider's own model list during the owner-approved live check
(`python -m media_os_api.ai.admin models`). Override without a code change via env vars:

* `AI_ROUTES`        — JSON `{"BENGALI_COPY": [["gemini", "<model>"], ...], ...}`
* `AI_QUOTA_LIMITS`  — JSON `{"gemini:<model>": {"rpm": 5, "rpd": 20,
  "reset_timezone": "..."}, ...}`

Credentials come only from the environment (`GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`,
`CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`). A provider whose credential is missing is simply
left out of every route.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import httpx

from media_os_api.ai.providers import (
    CloudflareProvider,
    GeminiProvider,
    GroqProvider,
    OpenRouterProvider,
    Provider,
)
from media_os_api.ai.quota import QuotaLimits, QuotaStore, QuotaTracker
from media_os_api.ai.router import AIRouter, AttemptCallback
from media_os_api.ai.types import ModelRoute, TaskClass
from media_os_api.config import PLACEHOLDER_PREFIXES
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.ratelimit import InMemoryMinIntervalLimiter, RateLimiter
from media_os_api.research.http.retry import RetryPolicy
from media_os_api.research.http.ssrf import Resolver, system_resolver

# Verified live on 2026-11-14 (model listing + one Bengali request each; see
# docs/evidence/n1_ai_live_check_2026-11-14.json). Re-verify with `ai.admin models` when changing.
GEMINI_FLASH = ModelRoute("gemini", "gemini-3.5-flash")  # best Bengali in the live check
GEMINI_FLASH_PREV = ModelRoute("gemini", "gemini-2.5-flash")
GEMINI_LITE = ModelRoute("gemini", "gemini-3.5-flash-lite")
GROQ_LARGE = ModelRoute("groq", "openai/gpt-oss-120b")  # reasoning model: keep max tokens >= 300
GROQ_SMALL = ModelRoute("groq", "openai/gpt-oss-20b")
GROQ_FAST = ModelRoute("groq", "qwen/qwen3.8-27b")
OPENROUTER_FREE = ModelRoute("openrouter", "google/gemma-4-31b-it:free")  # often busy upstream
CLOUDFLARE_LARGE = ModelRoute("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
CLOUDFLARE_GEMMA = ModelRoute("cloudflare", "@cf/google/gemma-4-26b-a4b-it")

# Bengali quality: Gemini first for anything a reader sees in Bengali.
DEFAULT_ROUTES: dict[TaskClass, list[ModelRoute]] = {
    TaskClass.BENGALI_COPY: [
        GEMINI_FLASH,
        GEMINI_FLASH_PREV,
        GROQ_LARGE,
        OPENROUTER_FREE,
        CLOUDFLARE_LARGE,
    ],
    TaskClass.ENGLISH_COPY: [GEMINI_LITE, GROQ_LARGE, CLOUDFLARE_LARGE, OPENROUTER_FREE],
    TaskClass.SIMPLE_FORMAT: [GROQ_FAST, GROQ_SMALL, CLOUDFLARE_LARGE, GEMINI_LITE],
    TaskClass.REVIEW: [GEMINI_FLASH, GROQ_LARGE, GEMINI_FLASH_PREV, CLOUDFLARE_GEMMA],
}

# Self-imposed caps at or below published/observed free tiers (dashboards are authoritative).
DEFAULT_LIMITS: dict[str, QuotaLimits] = {
    GEMINI_FLASH.key: QuotaLimits(rpm=5, rpd=20, reset_timezone="America/Los_Angeles"),
    GEMINI_FLASH_PREV.key: QuotaLimits(rpm=5, rpd=20, reset_timezone="America/Los_Angeles"),
    GEMINI_LITE.key: QuotaLimits(rpm=10, rpd=200, reset_timezone="America/Los_Angeles"),
    "groq": QuotaLimits(rpm=25, rpd=900),
    "openrouter": QuotaLimits(rpm=15, rpd=45),  # free models: 50/day without purchased credits
    "cloudflare": QuotaLimits(rpm=30, rpd=150),  # ~10k neurons/day shared across models
}


def routes_from_env(env: Mapping[str, str]) -> dict[TaskClass, list[ModelRoute]]:
    raw = env.get("AI_ROUTES")
    if not raw:
        return {k: list(v) for k, v in DEFAULT_ROUTES.items()}
    doc = json.loads(raw)
    return {
        TaskClass(task): [ModelRoute(str(p), str(m)) for p, m in pairs]
        for task, pairs in doc.items()
    }


def limits_from_env(env: Mapping[str, str]) -> dict[str, QuotaLimits]:
    limits = dict(DEFAULT_LIMITS)
    raw = env.get("AI_QUOTA_LIMITS")
    if raw:
        for key, spec in json.loads(raw).items():
            limits[str(key)] = QuotaLimits(
                rpm=spec.get("rpm"),
                rpd=spec.get("rpd"),
                reset_timezone=spec.get("reset_timezone", "UTC"),
            )
    return limits


def _credential(env: Mapping[str, str], name: str) -> str:
    """The env value, or "" when missing or still an `.env.example` placeholder."""
    value = env.get(name, "").strip()
    return "" if not value or value.upper().startswith(PLACEHOLDER_PREFIXES) else value


def providers_from_env(env: Mapping[str, str]) -> dict[str, Provider]:
    found: dict[str, Provider] = {}
    for cls in (GeminiProvider, GroqProvider, OpenRouterProvider):
        if _credential(env, cls.env_key):
            found[cls.name] = cls()
    account = _credential(env, "CLOUDFLARE_ACCOUNT_ID")
    if _credential(env, "CLOUDFLARE_API_TOKEN") and account:
        found["cloudflare"] = CloudflareProvider(account)
    return found


def build_router(
    *,
    quota_store: QuotaStore,
    environ: Mapping[str, str] | None = None,
    rate_limiter: RateLimiter | None = None,
    transport: httpx.BaseTransport | None = None,
    resolver: Resolver = system_resolver,
    on_attempt: AttemptCallback | None = None,
    sleep: Any = None,
) -> AIRouter:
    env = os.environ if environ is None else environ
    providers = providers_from_env(env)
    limits = limits_from_env(env)
    limiter = rate_limiter or InMemoryMinIntervalLimiter()

    def client_factory(provider: Provider) -> SafeHttpClient:
        rpm = (limits.get(provider.name) or QuotaLimits()).rpm
        kwargs: dict[str, Any] = {} if sleep is None else {"sleep": sleep}
        return SafeHttpClient(
            source_code=f"AI_{provider.name.upper()}",
            policy=provider.host_policy,
            rate_limiter=limiter,
            min_interval_ms=int(60000 / rpm) if rpm else 0,
            transport=transport,
            resolver=resolver,
            retry_policy=RetryPolicy(
                max_attempts=2, base_delay_seconds=1.0, retry_on_rate_limit=False
            ),
            bearer_token=_credential(env, provider.env_key),
            auth_header=provider.auth_header,
            auth_scheme=provider.auth_scheme,
            **kwargs,
        )

    return AIRouter(
        providers=providers,
        routes=routes_from_env(env),
        quota=QuotaTracker(quota_store, limits),
        client_factory=client_factory,
        on_attempt=on_attempt,
    )
