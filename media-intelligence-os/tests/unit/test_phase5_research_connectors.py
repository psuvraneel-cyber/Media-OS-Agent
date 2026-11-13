"""Unit tests for Phase 5 research connectors, sanitization, and policy enforcement."""

from __future__ import annotations

import pytest

from media_os_api.research.connectors import (
    GitHubConnector,
    HuggingFaceConnector,
    RedditConnector,
)
from media_os_api.research.models import PolicyBlockedError
from media_os_api.research.namespaces import (
    AUTHORIZED_NAMESPACES,
    BLOCKED_NAMESPACES,
    ResourceNamespace,
)
from media_os_api.research.sanitization import (
    compute_dedupe_key,
    parse_arxiv_id,
    sanitize_minimal_summary,
)

pytestmark = pytest.mark.unit


def test_resource_namespaces_and_policy_blocks() -> None:
    """Authorized namespaces must include all specified providers; Reddit must be blocked."""
    assert ResourceNamespace.GITHUB_REPOSITORY in AUTHORIZED_NAMESPACES
    assert ResourceNamespace.GITHUB_RELEASE in AUTHORIZED_NAMESPACES
    assert ResourceNamespace.HUGGINGFACE_MODEL in AUTHORIZED_NAMESPACES
    assert ResourceNamespace.HUGGINGFACE_DATASET in AUTHORIZED_NAMESPACES
    assert ResourceNamespace.HUGGINGFACE_SPACE in AUTHORIZED_NAMESPACES
    assert ResourceNamespace.HACKERNEWS_STORY in AUTHORIZED_NAMESPACES
    assert ResourceNamespace.ARXIV_PAPER in AUTHORIZED_NAMESPACES
    assert ResourceNamespace.OPENAI_NEWS in AUTHORIZED_NAMESPACES

    assert ResourceNamespace.REDDIT_POST in BLOCKED_NAMESPACES
    assert "REDDIT" in BLOCKED_NAMESPACES


def test_reddit_connector_strictly_blocked() -> None:
    """Instantiating or parsing through Reddit connector must fail with PolicyBlockedError."""
    with pytest.raises(PolicyBlockedError, match="strictly BLOCKED by policy"):
        RedditConnector()


def test_arxiv_id_parsing_and_version_extraction() -> None:
    """arXiv identifier parser must cleanly separate base paper ID from version tag."""
    base_id, ver = parse_arxiv_id("2403.12345v2")
    assert base_id == "2403.12345"
    assert ver == "v2"

    base_id2, ver2 = parse_arxiv_id("2403.12345")
    assert base_id2 == "2403.12345"
    assert ver2 is None

    base_id3, ver3 = parse_arxiv_id("hep-th/9901001v1")
    assert base_id3 == "hep-th/9901001"
    assert ver3 == "v1"


def test_arxiv_version_updates_map_to_same_topic_cluster() -> None:
    """Paper v1 and v2 must produce identical dedupe_key to prevent duplicate clusters."""
    key_v1 = compute_dedupe_key(
        ResourceNamespace.ARXIV_PAPER,
        "2403.12345v1",
        "https://arxiv.org/abs/2403.12345v1",
    )
    key_v2 = compute_dedupe_key(
        ResourceNamespace.ARXIV_PAPER,
        "2403.12345v2",
        "https://arxiv.org/abs/2403.12345v2",
    )
    assert key_v1 == "arxiv:paper:2403.12345"
    assert key_v2 == "arxiv:paper:2403.12345"
    assert key_v1 == key_v2


def test_github_connector_distinguishes_repository_and_release() -> None:
    """GitHub connector must distinguish repo from tagged release via namespaces."""
    repo_conn = GitHubConnector(resource_type=ResourceNamespace.GITHUB_REPOSITORY)
    cand_repo = repo_conn.parse_payload(
        {
            "full_name": "psuvraneel-cyber/Media-OS-Agent",
            "html_url": "https://github.com/psuvraneel-cyber/Media-OS-Agent",
            "description": "Multi-brand intelligence platform",
        }
    )
    assert cand_repo.source_provider == ResourceNamespace.GITHUB_REPOSITORY
    assert cand_repo.external_id == "psuvraneel-cyber/Media-OS-Agent"
    assert cand_repo.version is None

    release_conn = GitHubConnector(resource_type=ResourceNamespace.GITHUB_RELEASE)
    cand_rel = release_conn.parse_payload(
        {
            "full_name": "psuvraneel-cyber/Media-OS-Agent",
            "tag_name": "v1.0.0",
            "html_url": "https://github.com/psuvraneel-cyber/Media-OS-Agent/releases/tag/v1.0.0",
            "name": "Phase 4 Release",
        }
    )
    assert cand_rel.source_provider == ResourceNamespace.GITHUB_RELEASE
    assert cand_rel.external_id == "psuvraneel-cyber/Media-OS-Agent:v1.0.0"
    assert cand_rel.version == "v1.0.0"


def test_huggingface_connector_namespaces() -> None:
    """Hugging Face connector must support models, datasets, and spaces."""
    model_conn = HuggingFaceConnector(resource_type=ResourceNamespace.HUGGINGFACE_MODEL)
    cand = model_conn.parse_payload({"id": "meta-llama/Llama-3-8B"})
    assert cand.source_provider == ResourceNamespace.HUGGINGFACE_MODEL
    assert cand.external_id == "meta-llama/Llama-3-8B"
    assert "huggingface.co/meta-llama/Llama-3-8B" in cand.raw_url


def test_sanitize_minimal_summary() -> None:
    """Summary sanitizer must strip HTML, collapse spaces, and truncate to max length."""
    raw_html = "<p>First paragraph.</p><script>alert('xss')</script><b>Bold content</b>"
    cleaned = sanitize_minimal_summary(raw_html, max_length=100)
    assert cleaned == "First paragraph. alert('xss') Bold content"

    long_text = "A " * 300
    truncated = sanitize_minimal_summary(long_text, max_length=50)
    assert truncated is not None
    assert len(truncated) <= 50
    assert truncated.endswith("...")
