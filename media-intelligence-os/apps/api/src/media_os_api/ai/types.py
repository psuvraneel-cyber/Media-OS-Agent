"""Shared types for the free-tier multi-provider AI adapter (roadmap N1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel


class TaskClass(StrEnum):
    """What a generation step needs; the router maps each class to an ordered route list."""

    BENGALI_COPY = "BENGALI_COPY"  # on-image Bengali text, hooks, captions (quality-critical)
    ENGLISH_COPY = "ENGLISH_COPY"  # English captions for families abroad
    SIMPLE_FORMAT = "SIMPLE_FORMAT"  # hashtags, reformatting, short structured transforms
    REVIEW = "REVIEW"  # rubric scoring / QA review of a draft


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class GenerationRequest:
    task: TaskClass
    system: str
    prompt: str
    output_schema: type[BaseModel] | None = None
    max_output_tokens: int = 2048
    temperature: float = 0.7
    operation: str = "generate"  # free-form label recorded in the usage ledger


@dataclass(frozen=True)
class AttemptRecord:
    """One provider call. Never contains prompts, outputs or credentials."""

    provider: str
    model: str
    outcome: str  # OK | QUOTA | RATE_LIMITED | TRANSIENT | AUTH | NOT_FOUND | INVALID_OUTPUT | ...
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class GenerationResult:
    provider: str
    model: str
    text: str
    data: BaseModel | None
    input_tokens: int
    output_tokens: int
    attempts: list[AttemptRecord] = field(default_factory=list)
