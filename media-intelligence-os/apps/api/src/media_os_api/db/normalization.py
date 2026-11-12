"""Deterministic canonical URL normalization and content hashing utilities."""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_QUERY_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "source",
        "utm_campaign",
        "utm_content",
        "utm_id",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
)


def normalize_canonical_url(raw_url: str) -> str:
    """Normalize a source URL deterministically for deduplication and provenance."""
    cleaned = raw_url.strip()
    parsed = urlparse(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Canonical URL must use http:// or https:// with a valid host: {raw_url}")

    hostname = parsed.hostname.lower()
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = hostname
    elif port is not None:
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    filtered_query = sorted(
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_QUERY_PARAMS
    )
    query = urlencode(filtered_query)

    return urlunparse((scheme, netloc, path, "", query, ""))


def compute_content_hash(content: str) -> str:
    """Return deterministic SHA-256 hex digest over normalized UTF-8 text."""
    normalized = " ".join(content.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_record_hash(payload: dict[str, object]) -> str:
    """Return deterministic SHA-256 hex digest over JSON-canonicalized metadata."""
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
