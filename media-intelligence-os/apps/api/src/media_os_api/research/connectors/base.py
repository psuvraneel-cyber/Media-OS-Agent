"""Base connector contract and source policy enforcement for Phase 5 research connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from media_os_api.research.models import PolicyBlockedError, RawDiscoveryCandidate
from media_os_api.research.namespaces import BLOCKED_NAMESPACES, SourcePolicyMode


class BaseConnector(ABC):
    """Abstract base class for all external research discovery connectors."""

    provider_namespace: str
    default_policy_mode: SourcePolicyMode = SourcePolicyMode.EXPERIMENTAL
    rate_limit_per_minute: int = 30

    def __init__(self, policy_mode: SourcePolicyMode | None = None) -> None:
        self.policy_mode = policy_mode or self.default_policy_mode
        self._enforce_policy_compliance()

    def _enforce_policy_compliance(self) -> None:
        """Enforce non-negotiable source policy gates (e.g., Reddit block)."""
        if self.provider_namespace in BLOCKED_NAMESPACES:
            raise PolicyBlockedError(
                f"Source provider '{self.provider_namespace}' is strictly BLOCKED by policy. "
                f"Automated ingestion from this source is prohibited."
            )
        if self.policy_mode == SourcePolicyMode.BLOCKED:
            raise PolicyBlockedError(
                f"Connector for '{self.provider_namespace}' is currently configured as BLOCKED."
            )

    @abstractmethod
    def parse_payload(self, raw_item: dict[str, Any]) -> RawDiscoveryCandidate:
        """Parse an untrusted external record into a validated RawDiscoveryCandidate."""
