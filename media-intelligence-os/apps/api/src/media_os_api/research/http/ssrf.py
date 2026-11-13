"""SSRF defences for research fetches: scheme, exact-host allowlist, and resolved-address checks.

Every hop (initial request and each redirect) is validated *before* connecting:

1. scheme must be `https`; no userinfo; default port only;
2. host must be in the source's exact allowlist and must not be an IP literal;
3. every address the host resolves to must be globally routable — loopback, RFC 1918, link-local
   (incl. cloud metadata 169.254.169.254), CGNAT 100.64/10, ULA fc00::/7, multicast, reserved and
   IPv4-mapped forms of those are rejected.

Residual risk: httpx resolves the name again when connecting (DNS-rebinding TOCTOU). The client
mitigates this by re-checking the connected peer address when the transport exposes it, and the
allowlist only contains large first-party API hosts.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable

import httpx

from media_os_api.research.http.errors import RetryableFetchError, UnsafeUrlError
from media_os_api.research.http.policy import HostPolicy

Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int) -> list[str]:
    """Resolve a hostname to its set of IP address strings using the OS resolver."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RetryableFetchError(
            f"DNS resolution failed for '{host}'.", code="DNS_RESOLUTION_FAILED"
        ) from exc
    return sorted({str(info[4][0]) for info in infos})


def is_public_address(raw: str) -> bool:
    """True only for globally routable unicast addresses (IPv4-mapped IPv6 unwrapped)."""
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_private
        and not ip.is_unspecified
    )


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def validate_url(url: str | httpx.URL, policy: HostPolicy, resolver: Resolver) -> httpx.URL:
    """Return the parsed URL if it is safe to request under `policy`, else raise UnsafeUrlError."""
    try:
        parsed = url if isinstance(url, httpx.URL) else httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise UnsafeUrlError("Malformed URL.") from exc

    if parsed.scheme != "https":
        raise UnsafeUrlError(f"Only https URLs are permitted (got '{parsed.scheme}').")
    if parsed.userinfo:
        raise UnsafeUrlError("URLs with embedded credentials are not permitted.")
    host = (parsed.host or "").lower()
    if not host or _is_ip_literal(host):
        raise UnsafeUrlError("A hostname is required; IP-literal URLs are not permitted.")
    if host not in policy.allowed_hosts:
        raise UnsafeUrlError(f"Host '{host}' is not in this source's allowlist.")
    if parsed.port not in (None, 443):
        raise UnsafeUrlError("Only the default https port is permitted.")

    addresses = resolver(host, 443)
    if not addresses:
        raise RetryableFetchError(f"No addresses resolved for '{host}'.", code="DNS_NO_ADDRESSES")
    blocked = [a for a in addresses if not is_public_address(a)]
    if blocked:
        raise UnsafeUrlError(f"Host '{host}' resolves to a non-public address; refusing.")
    return parsed
