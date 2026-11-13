"""Owner CLI for the free-tier AI adapter. Both commands make live provider calls: run them only
with owner approval.

    python -m media_os_api.ai.admin providers          # which providers have credentials (no calls)
    python -m media_os_api.ai.admin models             # list models each provider offers
    python -m media_os_api.ai.admin ping --task ENGLISH_COPY   # one tiny generation

Credentials are read from the environment and never printed.
"""

from __future__ import annotations

import argparse
import json
import sys

from media_os_api.ai.config import build_router, limits_from_env, providers_from_env
from media_os_api.ai.quota import InMemoryQuotaStore, QuotaLimits, QuotaStore, RedisQuotaStore
from media_os_api.ai.types import GenerationRequest, TaskClass
from media_os_api.config import load_settings
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.errors import PermanentFetchError, RetryableFetchError
from media_os_api.research.http.ratelimit import InMemoryMinIntervalLimiter
from media_os_api.research.http.retry import RetryPolicy


def _quota_store() -> QuotaStore:
    settings = load_settings()
    if settings.redis_url is None:
        return InMemoryQuotaStore()
    from redis import Redis

    return RedisQuotaStore(
        Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=2.0, socket_connect_timeout=2.0
        )
    )


def cmd_providers() -> int:
    import os

    print(json.dumps({"configured": sorted(providers_from_env(os.environ))}))
    return 0


def cmd_models() -> int:
    import os

    out: dict[str, object] = {}
    limits = limits_from_env(os.environ)
    for name, provider in sorted(providers_from_env(os.environ).items()):
        rpm = (limits.get(name) or QuotaLimits()).rpm
        client = SafeHttpClient(
            source_code=f"AI_{name.upper()}",
            policy=provider.host_policy,
            rate_limiter=InMemoryMinIntervalLimiter(),
            min_interval_ms=int(60000 / rpm) if rpm else 0,
            retry_policy=RetryPolicy(max_attempts=1, retry_on_rate_limit=False),
            bearer_token=os.environ.get(provider.env_key, ""),
            auth_header=provider.auth_header,
            auth_scheme=provider.auth_scheme,
        )
        try:
            out[name] = provider.parse_models(client.get(provider.models_endpoint()).body)
        except (PermanentFetchError, RetryableFetchError, ValueError) as exc:
            out[name] = {"error": type(exc).__name__, "code": getattr(exc, "code", None)}
        finally:
            client.close()
    print(json.dumps(out, indent=2))
    return 0


def cmd_ping(task: str) -> int:
    router = build_router(quota_store=_quota_store())
    try:
        result = router.generate(
            GenerationRequest(
                task=TaskClass(task),
                system="You are a terse assistant.",
                prompt="Reply with the single word: ok",
                max_output_tokens=16,
                temperature=0.0,
                operation="ping",
            )
        )
    finally:
        router.close()
    print(
        json.dumps(
            {
                "provider": result.provider,
                "model": result.model,
                "reply": result.text.strip()[:40],
                "attempts": [f"{a.provider}/{a.model}={a.outcome}" for a in result.attempts],
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="media_os_api.ai.admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("providers")
    sub.add_parser("models")
    ping = sub.add_parser("ping")
    ping.add_argument(
        "--task", default=TaskClass.ENGLISH_COPY.value, choices=[t.value for t in TaskClass]
    )
    args = parser.parse_args(argv)
    if args.cmd == "providers":
        return cmd_providers()
    if args.cmd == "models":
        return cmd_models()
    return cmd_ping(args.task)


if __name__ == "__main__":
    sys.exit(main())
