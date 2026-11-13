"""Fixture-based unit tests for the Hugging Face fetcher (no network, no database)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from tests.research_fixtures import HuggingFaceFixtureUpstream, make_hf_http

from media_os_api.research.fetchers.base import FetchPage, StreamSpec
from media_os_api.research.fetchers.huggingface import API_BASE, HuggingFaceFetcher
from media_os_api.research.http import PermanentFetchError
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState

pytestmark = pytest.mark.unit

MODELS = StreamSpec("models")


def _fetch(
    upstream: HuggingFaceFixtureUpstream,
    stream: StreamSpec = MODELS,
    checkpoint: CheckpointState | None = None,
    config: Mapping[str, object] | None = None,
) -> FetchPage:
    with make_hf_http(upstream) as http:
        return HuggingFaceFetcher().fetch_page(
            http, stream, checkpoint or CheckpointState(), config or {}
        )


def test_official_api_and_policy() -> None:
    assert API_BASE == "https://huggingface.co/api"
    assert HuggingFaceFetcher.host_policy.allowed_hosts == frozenset({"huggingface.co"})
    assert HuggingFaceFetcher.host_policy.auth_hosts == frozenset()
    f = HuggingFaceFetcher()
    assert [s.name for s in f.streams({})] == ["models", "datasets", "spaces"]
    assert [s.name for s in f.streams({"streams": ["spaces", "weights", "models"]})] == [
        "spaces",
        "models",
    ]
    assert f.sort_key({"sort": "likes"}) == "likes"
    assert f.sort_key({"sort": "evil&x=1"}) == "trendingScore"
    assert f.limit({"limit": 10_000}) == 100


def test_request_shape_listing_only() -> None:
    upstream = HuggingFaceFixtureUpstream()
    _fetch(upstream, config={"limit": 5, "sort": "likes"})
    req = upstream.requests[0]
    assert str(req.url).startswith("https://huggingface.co/api/models?")
    assert dict(req.url.params) == {"sort": "likes", "direction": "-1", "limit": "5"}
    assert "authorization" not in req.headers


def test_records_filtering_and_seen_window() -> None:
    page = _fetch(HuggingFaceFixtureUpstream())
    f = HuggingFaceFetcher()
    outcomes: list[str] = []
    for r in page.records:
        try:
            outcomes.append("ok" if f.to_candidates(r) else "filtered")
        except ValueError:
            outcomes.append("malformed")
    assert outcomes == ["ok", "ok", "ok", "filtered", "malformed", "malformed"]
    again = _fetch(HuggingFaceFixtureUpstream(), checkpoint=page.checkpoint)
    assert again.records == []


def test_candidates_per_resource_type() -> None:
    f = HuggingFaceFetcher()
    (m,) = f.to_candidates(_fetch(HuggingFaceFixtureUpstream()).records[0])
    assert m.source_provider == ResourceNamespace.HUGGINGFACE_MODEL
    assert m.raw_url == "https://huggingface.co/fixture-lab/tiny-coder-1b"
    assert m.published_at == datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    assert m.metadata == {"downloads": 12000, "likes": 512, "pipeline_tag": "text-generation"}
    (d,) = f.to_candidates(_fetch(HuggingFaceFixtureUpstream(), StreamSpec("datasets")).records[0])
    assert d.source_provider == ResourceNamespace.HUGGINGFACE_DATASET
    assert d.raw_url == "https://huggingface.co/datasets/fixture-lab/bengali-wedding-texts"
    (s,) = f.to_candidates(_fetch(HuggingFaceFixtureUpstream(), StreamSpec("spaces")).records[0])
    assert s.raw_url == "https://huggingface.co/spaces/fixture-lab/demo-space"


@pytest.mark.parametrize("body", [b"{bad", b'{"items": []}'])
def test_malformed_listing(body: bytes) -> None:
    upstream = HuggingFaceFixtureUpstream()
    upstream.bodies["models"] = body
    with pytest.raises(ValueError):
        _fetch(upstream)


def test_http_errors_propagate() -> None:
    upstream = HuggingFaceFixtureUpstream()
    upstream.status_override["models"] = 404
    with pytest.raises(PermanentFetchError):
        _fetch(upstream)


def test_bounded_config() -> None:
    f = HuggingFaceFetcher()
    cfg = f.bounded_config({"streams": ["models", "spaces"]}, StreamSpec("spaces"), 4)
    assert [s.name for s in f.streams(cfg)] == ["spaces"] and f.limit(cfg) == 4
