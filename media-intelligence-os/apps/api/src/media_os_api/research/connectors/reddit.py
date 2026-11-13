"""Policy gate blocking Reddit connector execution."""

from __future__ import annotations

from typing import Any

from media_os_api.research.connectors.base import BaseConnector
from media_os_api.research.models import PolicyBlockedError, RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace, SourcePolicyMode


class RedditConnector(BaseConnector):
    """Reddit connector stub strictly enforcing policy block.

    Prohibited from executing until formal owner authorization and API Terms review.
    """

    provider_namespace = ResourceNamespace.REDDIT_POST
    default_policy_mode = SourcePolicyMode.BLOCKED

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise PolicyBlockedError(
            "Reddit connector is strictly BLOCKED by policy (SRC-01). "
            "Automated scraping and API ingestion from Reddit are disabled."
        )

    def parse_payload(self, raw_item: dict[str, Any]) -> RawDiscoveryCandidate:
        raise PolicyBlockedError("Cannot parse payload from BLOCKED source provider 'REDDIT'.")
