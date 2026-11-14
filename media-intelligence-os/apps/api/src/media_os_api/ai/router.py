"""Seamless free-tier failover across *different* providers (never multiple keys of one provider).

For each generation step the router walks the task's ordered route list:

* route blocked or over its self-imposed cap → skip instantly;
* provider says 429 / quota → block that route (until `Retry-After`, or until the daily reset after
  repeated refusals) and retry **the same step** on the next route without pausing;
* timeouts / 5xx → per-provider circuit breaker (3 failures in 5 min → provider paused 5 min);
* 401/403 (bad key) → provider paused 6 h; 404 (model gone) → route paused 24 h;
* response missing or invalid for the requested schema → one repair attempt on the same route, then
  the next route; a reply cut off by the output-token limit goes straight to the next route.

Only when **every** route is unavailable does the step stop: `AllProvidersExhausted`
(retryable, with `resume_at` = earliest reset), so the durable job resumes later instead of failing.
Prompts, outputs and credentials are never logged or recorded.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from media_os_api.ai.errors import AIUnavailable, AllProvidersExhausted, InvalidModelOutput
from media_os_api.ai.providers import Provider
from media_os_api.ai.quota import QuotaTracker
from media_os_api.ai.types import (
    AttemptRecord,
    GenerationRequest,
    GenerationResult,
    ModelRoute,
    TaskClass,
)
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.errors import PermanentFetchError, RetryableFetchError

logger = logging.getLogger("media_os_api.ai.router")

ClientFactory = Callable[[Provider], SafeHttpClient]
AttemptCallback = Callable[[GenerationRequest, AttemptRecord], None]

SHORT_RATE_LIMIT = timedelta(seconds=120)
REPEATED_429_FOR_DAILY_BLOCK = 3
BREAKER_FAILURES = 3
BREAKER_PAUSE = timedelta(minutes=5)
AUTH_PAUSE = timedelta(hours=6)
NOT_FOUND_PAUSE = timedelta(hours=24)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
# A repair prompt cannot fix these; asking again would only spend another request.
NO_REPAIR_CODES = frozenset({"BLOCKED", "BAD_RESPONSE", "PROVIDER_ERROR", "TRUNCATED"})
REPAIR_SUFFIX = (
    "\n\nYour previous reply could not be parsed. Reply with ONLY one JSON object that matches the "
    "requested fields exactly. No prose, no code fences."
)


def extract_json(text: str) -> Any:
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise InvalidModelOutput("No JSON object in reply.") from None
        try:
            return json.loads(cleaned[start : end + 1])
        except ValueError as exc:
            raise InvalidModelOutput("Reply contains malformed JSON.") from exc


def validate_output(text: str, schema: type[BaseModel] | None) -> BaseModel | None:
    if schema is None:
        return None
    try:
        return schema.model_validate(extract_json(text))
    except ValidationError as exc:
        raise InvalidModelOutput(f"Reply does not match {schema.__name__}.") from exc


class AIRouter:
    def __init__(
        self,
        *,
        providers: Mapping[str, Provider],
        routes: Mapping[TaskClass, Sequence[ModelRoute]],
        quota: QuotaTracker,
        client_factory: ClientFactory,
        on_attempt: AttemptCallback | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._providers = dict(providers)
        self._routes = {
            task: [r for r in rs if r.provider in self._providers] for task, rs in routes.items()
        }
        self._quota = quota
        self._client_factory = client_factory
        self._clients: dict[str, SafeHttpClient] = {}
        self._on_attempt = on_attempt
        self._now = now

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def routes_for(self, task: TaskClass) -> list[ModelRoute]:
        return list(self._routes.get(task, []))

    def _client(self, provider: Provider) -> SafeHttpClient:
        if provider.name not in self._clients:
            self._clients[provider.name] = self._client_factory(provider)
        return self._clients[provider.name]

    def _record(
        self, request: GenerationRequest, attempt: AttemptRecord, attempts: list[AttemptRecord]
    ) -> None:
        attempts.append(attempt)
        if self._on_attempt is not None:
            self._on_attempt(request, attempt)
        logger.info(
            "ai_attempt",
            extra={
                "provider": attempt.provider,
                "model": attempt.model,
                "task": request.task.value,
                "outcome": attempt.outcome,
            },
        )

    def _call(self, route: ModelRoute, request: GenerationRequest, prompt: str) -> GenerationResult:
        provider = self._providers[route.provider]
        effective = GenerationRequest(
            task=request.task,
            system=request.system,
            prompt=prompt,
            output_schema=request.output_schema,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            operation=request.operation,
        )
        result = self._client(provider).post_json(
            provider.endpoint(route.model), provider.payload(route.model, effective)
        )
        parsed = provider.parse(result.body)
        data = validate_output(parsed.text, request.output_schema)
        return GenerationResult(
            route.provider,
            route.model,
            parsed.text,
            data,
            parsed.input_tokens,
            parsed.output_tokens,
        )

    def _handle_rate_limit(self, route: ModelRoute, exc: RetryableFetchError) -> str:
        now = self._now()
        day = now.date().isoformat()
        count = self._quota.bump(f"429:{route.key}:{day}", 2 * 86400)
        wait = exc.retry_after
        if wait is not None and wait <= SHORT_RATE_LIMIT and count < REPEATED_429_FOR_DAILY_BLOCK:
            self._quota.block_route(route, now + wait)
            return "RATE_LIMITED"
        until = now + wait if wait is not None and wait > SHORT_RATE_LIMIT else None
        if until is None:
            until = (
                self._quota.quota_reset_for(route)
                if count >= REPEATED_429_FOR_DAILY_BLOCK
                else now + timedelta(seconds=60)
            )
        self._quota.block_route(route, until)
        return "QUOTA"

    def _handle_transient(self, route: ModelRoute) -> None:
        now = self._now()
        bucket = int(now.timestamp() // 300)
        fails = self._quota.bump(f"breaker:{route.provider}:{bucket}", 600)
        if fails >= BREAKER_FAILURES:
            self._quota.block_provider(route.provider, now + BREAKER_PAUSE)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        attempts: list[AttemptRecord] = []
        quota_limited = 0
        routes = self.routes_for(request.task)
        if not routes:
            raise AIUnavailable(f"No configured provider for task {request.task}.")

        for route in routes:
            if not self._quota.reserve(route):
                quota_limited += 1
                self._record(
                    request, AttemptRecord(route.provider, route.model, "SKIPPED_QUOTA"), attempts
                )
                continue
            prompt = request.prompt
            for repair in (False, True):
                try:
                    result = self._call(route, request, prompt)
                except RetryableFetchError as exc:
                    if exc.code in {"HTTP_429", "HTTP_403_RATE_LIMITED"}:
                        outcome = self._handle_rate_limit(route, exc)
                        quota_limited += 1
                    else:
                        self._handle_transient(route)
                        outcome = "TRANSIENT"
                    self._record(
                        request, AttemptRecord(route.provider, route.model, outcome), attempts
                    )
                    break
                except PermanentFetchError as exc:
                    if exc.status_code in (401, 403):
                        self._quota.block_provider(route.provider, self._now() + AUTH_PAUSE)
                        outcome = "AUTH"
                    elif exc.status_code == 404:
                        self._quota.block_route(route, self._now() + NOT_FOUND_PAUSE)
                        outcome = "NOT_FOUND"
                    else:
                        outcome = exc.code[:32]
                    self._record(
                        request, AttemptRecord(route.provider, route.model, outcome), attempts
                    )
                    break
                except (InvalidModelOutput, ValueError) as exc:
                    code = getattr(exc, "code", "INVALID_OUTPUT")
                    self._record(
                        request, AttemptRecord(route.provider, route.model, code), attempts
                    )
                    if repair or code in NO_REPAIR_CODES:
                        break
                    if not self._quota.reserve(route):
                        quota_limited += 1
                        break
                    prompt = request.prompt + REPAIR_SUFFIX
                    continue
                ok = AttemptRecord(
                    route.provider, route.model, "OK", result.input_tokens, result.output_tokens
                )
                self._record(request, ok, attempts)
                result.attempts = attempts
                return result

        resume_candidates = [t for t in (self._quota.blocked_until(r) for r in routes) if t]
        if quota_limited and len(resume_candidates) == len(routes):
            resume_at = min(resume_candidates)
            raise AllProvidersExhausted(
                f"All {len(routes)} routes for {request.task} are out of free quota; "
                f"resume at {resume_at.isoformat()}.",
                resume_at=resume_at,
            )
        raise AIUnavailable(
            f"No route produced a valid result for {request.task}: "
            + ", ".join(f"{a.provider}/{a.model}={a.outcome}" for a in attempts)
        )
