"""Provider adapters: build one request and parse one response. Transport, SSRF policy, limits and
retries are handled by `SafeHttpClient`; credentials are injected there and never touch payloads.

Supported (free tiers, no billing):
* Gemini        — https://generativelanguage.googleapis.com  (`x-goog-api-key` header)
* Groq          — https://api.groq.com/openai/v1             (OpenAI-compatible, Bearer)
* OpenRouter    — https://openrouter.ai/api/v1               (OpenAI-compatible, Bearer; `:free`)
* Cloudflare AI — https://api.cloudflare.com/client/v4/accounts/{id}/ai/run/{model} (Bearer)
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import quote

from media_os_api.ai.errors import InvalidModelOutput
from media_os_api.ai.types import GenerationRequest
from media_os_api.research.http.policy import HostPolicy

_AI_MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ParsedCompletion:
    text: str
    input_tokens: int
    output_tokens: int


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidModelOutput("Provider response is not JSON.", code="BAD_RESPONSE") from exc


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class Provider(ABC):
    name: ClassVar[str]
    env_key: ClassVar[str]  # environment variable holding the credential
    auth_header: ClassVar[str] = "Authorization"
    auth_scheme: ClassVar[str | None] = "Bearer"
    host_policy: ClassVar[HostPolicy]
    model_pattern: ClassVar[re.Pattern[str]]

    def validate_model(self, model: str) -> str:
        if not self.model_pattern.match(model):
            raise ValueError(f"Invalid model id for {self.name}: {model!r}")
        return model

    @abstractmethod
    def endpoint(self, model: str) -> str: ...

    @abstractmethod
    def payload(self, model: str, request: GenerationRequest) -> dict[str, Any]: ...

    @abstractmethod
    def parse(self, body: bytes) -> ParsedCompletion: ...

    @abstractmethod
    def models_endpoint(self) -> str: ...

    @abstractmethod
    def parse_models(self, body: bytes) -> list[str]: ...


def _policy(host: str, read_timeout: float = 90.0) -> HostPolicy:
    return HostPolicy(
        allowed_hosts=frozenset({host}),
        auth_hosts=frozenset({host}),
        max_response_bytes=_AI_MAX_BYTES,
        read_timeout_seconds=read_timeout,
        max_redirects=0,
    )


class GeminiProvider(Provider):
    name = "gemini"
    env_key = "GEMINI_API_KEY"
    auth_header = "x-goog-api-key"
    auth_scheme = None
    host_policy = _policy("generativelanguage.googleapis.com")
    model_pattern = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,80}$")
    base = "https://generativelanguage.googleapis.com/v1beta"

    def endpoint(self, model: str) -> str:
        return f"{self.base}/models/{self.validate_model(model)}:generateContent"

    def payload(self, model: str, request: GenerationRequest) -> dict[str, Any]:
        config: dict[str, Any] = {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_output_tokens,
        }
        if request.output_schema is not None:
            config["responseMimeType"] = "application/json"
        return {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": config,
        }

    def parse(self, body: bytes) -> ParsedCompletion:
        doc = _json(body)
        candidates = doc.get("candidates") if isinstance(doc, dict) else None
        if not candidates:
            reason = (
                (doc.get("promptFeedback") or {}).get("blockReason")
                if isinstance(doc, dict)
                else None
            )
            raise InvalidModelOutput(
                f"Gemini returned no candidates ({reason or 'empty'}).",
                code="BLOCKED" if reason else "EMPTY",
            )
        parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        if not text.strip():
            raise InvalidModelOutput("Gemini returned an empty candidate.", code="EMPTY")
        usage = doc.get("usageMetadata") or {}
        return ParsedCompletion(
            text, _int(usage.get("promptTokenCount")), _int(usage.get("candidatesTokenCount"))
        )

    def models_endpoint(self) -> str:
        return f"{self.base}/models"

    def parse_models(self, body: bytes) -> list[str]:
        doc = _json(body)
        out = []
        for m in (doc.get("models") or []) if isinstance(doc, dict) else []:
            name = str(m.get("name", ""))
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                out.append(name.removeprefix("models/"))
        return sorted(out)


class _OpenAICompatible(Provider):
    base: ClassVar[str]
    model_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:]{1,120}$")

    def endpoint(self, model: str) -> str:
        self.validate_model(model)
        return f"{self.base}/chat/completions"

    def payload(self, model: str, request: GenerationRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.validate_model(model),
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.output_schema is not None:
            body["response_format"] = {"type": "json_object"}
        return body

    def parse(self, body: bytes) -> ParsedCompletion:
        doc = _json(body)
        if not isinstance(doc, dict):
            raise InvalidModelOutput("Unexpected response shape.", code="BAD_RESPONSE")
        if doc.get("error"):
            raise InvalidModelOutput("Provider returned an error object.", code="PROVIDER_ERROR")
        choices = doc.get("choices") or []
        message = (choices[0] or {}).get("message") if choices else None
        text = str((message or {}).get("content") or "")
        if not text.strip():
            raise InvalidModelOutput("Empty completion.", code="EMPTY")
        usage = doc.get("usage") or {}
        return ParsedCompletion(
            text, _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))
        )

    def models_endpoint(self) -> str:
        return f"{self.base}/models"

    def parse_models(self, body: bytes) -> list[str]:
        doc = _json(body)
        data = doc.get("data") if isinstance(doc, dict) else None
        return sorted(str(m.get("id")) for m in (data or []) if isinstance(m, dict) and m.get("id"))


class GroqProvider(_OpenAICompatible):
    name = "groq"
    env_key = "GROQ_API_KEY"
    host_policy = _policy("api.groq.com")
    base = "https://api.groq.com/openai/v1"


class OpenRouterProvider(_OpenAICompatible):
    name = "openrouter"
    env_key = "OPENROUTER_API_KEY"
    host_policy = _policy("openrouter.ai")
    base = "https://openrouter.ai/api/v1"

    def validate_model(self, model: str) -> str:
        # Zero-budget policy: only ':free' variants may ever be called through OpenRouter.
        super().validate_model(model)
        if not model.endswith(":free"):
            raise ValueError("OpenRouter models must be ':free' variants (zero-budget policy).")
        return model

    def parse_models(self, body: bytes) -> list[str]:
        return [m for m in super().parse_models(body) if m.endswith(":free")]


class CloudflareProvider(Provider):
    name = "cloudflare"
    env_key = "CLOUDFLARE_API_TOKEN"
    host_policy = _policy("api.cloudflare.com")
    model_pattern = re.compile(r"^@cf/[a-z0-9\-]+/[a-z0-9.\-]+$")
    _account_pattern = re.compile(r"^[0-9a-f]{32}$")

    def __init__(self, account_id: str) -> None:
        if not self._account_pattern.match(account_id):
            raise ValueError("CLOUDFLARE_ACCOUNT_ID must be a 32-character hex id.")
        self._base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai"

    def endpoint(self, model: str) -> str:
        return f"{self._base}/run/{quote(self.validate_model(model), safe='@/.-')}"

    def payload(self, model: str, request: GenerationRequest) -> dict[str, Any]:
        system = request.system
        if request.output_schema is not None:
            system += "\nReply with a single JSON object only."
        return {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }

    def parse(self, body: bytes) -> ParsedCompletion:
        doc = _json(body)
        if not isinstance(doc, dict) or doc.get("success") is False:
            raise InvalidModelOutput("Cloudflare reported failure.", code="PROVIDER_ERROR")
        result = doc.get("result") or {}
        response = result.get("response")
        text = response if isinstance(response, str) else json.dumps(response) if response else ""
        if not text.strip():
            raise InvalidModelOutput("Empty completion.", code="EMPTY")
        usage = result.get("usage") or {}
        return ParsedCompletion(
            text, _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))
        )

    def models_endpoint(self) -> str:
        return f"{self._base}/models/search?task=Text%20Generation"

    def parse_models(self, body: bytes) -> list[str]:
        doc = _json(body)
        result = doc.get("result") if isinstance(doc, dict) else None
        return sorted(
            str(m.get("name")) for m in (result or []) if isinstance(m, dict) and m.get("name")
        )
