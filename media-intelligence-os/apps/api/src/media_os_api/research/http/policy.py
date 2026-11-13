"""Per-source outbound HTTP policy: exact-host allowlist, timeouts, and body size cap."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_USER_AGENT = "MediaIntelligenceOS/0.5 (+local research ingestion; official APIs and feeds)"


@dataclass(frozen=True)
class HostPolicy:
    """Outbound policy for one source. Hosts are matched exactly (no wildcard/suffix matching)."""

    allowed_hosts: frozenset[str]
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 15.0
    pool_timeout_seconds: float = 5.0
    max_redirects: int = 3
    # Hosts that may receive the optional Authorization header (subset of allowed_hosts).
    auth_hosts: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.allowed_hosts:
            raise ValueError("HostPolicy requires at least one allowed host.")
        for host in self.allowed_hosts | self.auth_hosts:
            if host != host.lower() or "/" in host or ":" in host or "*" in host:
                raise ValueError(f"Allowed host '{host}' must be a bare lowercase hostname.")
        if not self.auth_hosts <= self.allowed_hosts:
            raise ValueError("auth_hosts must be a subset of allowed_hosts.")
        if self.max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be at least 1 KiB.")
        if not 0 <= self.max_redirects <= 5:
            raise ValueError("max_redirects must be between 0 and 5.")
