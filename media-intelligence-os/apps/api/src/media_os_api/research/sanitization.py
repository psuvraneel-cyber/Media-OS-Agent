"""Sanitization, version extraction, and deterministic deduplication for research candidates."""

from __future__ import annotations

import hashlib
import html
import re

from media_os_api.db.normalization import normalize_canonical_url
from media_os_api.research.namespaces import ResourceNamespace

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_ARXIV_VERSION_PATTERN = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})v(\d+)$", re.I)


def sanitize_minimal_summary(raw: str | None, max_length: int = 500) -> str | None:
    """Sanitize raw summary, stripping HTML and truncating to strict discovery boundary.

    Enforces that untrusted payloads never retain full body texts, comments, or markups.
    """
    if not raw:
        return None
    # Strip tags, decode entities (e.g. HN's "&#x27;"), then strip again so encoded markup such
    # as "&lt;script&gt;" cannot survive as a tag after decoding.
    cleaned = _HTML_TAG_PATTERN.sub(" ", raw)
    cleaned = html.unescape(cleaned)
    cleaned = _HTML_TAG_PATTERN.sub(" ", cleaned)
    # Drop control characters (keep normal whitespace, collapsed below)
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() or ch.isspace())
    # Collapse whitespace
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        return cleaned[: max_length - 3].rstrip() + "..."
    return cleaned


def parse_arxiv_id(raw_id: str) -> tuple[str, str | None]:
    """Extract the base arXiv paper identifier and optional version tag.

    Examples:
        '2403.12345v2' -> ('2403.12345', 'v2')
        '2403.12345'   -> ('2403.12345', None)
        'hep-th/9901001v1' -> ('hep-th/9901001', 'v1')
    """
    cleaned = raw_id.strip()
    match = _ARXIV_VERSION_PATTERN.match(cleaned)
    if match:
        base_id, version_num = match.groups()
        return base_id, f"v{version_num}"
    return cleaned, None


def compute_dedupe_key(
    source_provider: str,
    external_id: str,
    raw_url: str,
    version: str | None = None,
) -> str:
    """Compute a conservative, deterministic deduplication key for topic clustering.

    Merges exact matches via canonical URLs and stable domain identifiers,
    while mapping paper version updates to the same parent topic cluster.
    """
    # 1. Specialized handling for versioned arXiv papers
    if source_provider == ResourceNamespace.ARXIV_PAPER:
        base_id, extracted_ver = parse_arxiv_id(external_id)
        return f"arxiv:paper:{base_id.lower()}"

    # 2. Canonical URL normalization
    canonical_url = normalize_canonical_url(raw_url)

    # 3. GitHub repository deduplication
    if source_provider == ResourceNamespace.GITHUB_REPOSITORY:
        norm_id = external_id.strip().lower()
        return f"github:repo:{norm_id}"

    # 4. GitHub release deduplication
    if source_provider == ResourceNamespace.GITHUB_RELEASE:
        norm_id = external_id.strip().lower()
        return f"github:release:{norm_id}"

    # 5. Hugging Face resource deduplication
    if source_provider.startswith("HUGGINGFACE_"):
        sub_type = source_provider.replace("HUGGINGFACE_", "").lower()
        norm_id = external_id.strip().lower()
        return f"hf:{sub_type}:{norm_id}"

    # 6. Default conservative deduplication: hash of canonical URL
    url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    return f"url:{url_hash}"
