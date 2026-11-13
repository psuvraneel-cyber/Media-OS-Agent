"""Fixture-based unit tests for the arXiv fetcher (no network, no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.research_fixtures import ARXIV_FIXTURES, ArxivFixtureUpstream, make_arxiv_http

from media_os_api.research.fetchers.arxiv import (
    API_URL,
    HARD_MAX_PAGE_SIZE,
    MAX_OFFSET,
    ArxivFetcher,
)
from media_os_api.research.fetchers.base import StreamSpec
from media_os_api.research.http import PermanentFetchError
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState
from media_os_api.research.sanitization import compute_dedupe_key, sanitize_minimal_summary

pytestmark = pytest.mark.unit

AI = StreamSpec("cat:cs.AI", {"category": "cs.AI"})


def _entries(n: int, *, day: int = 12, prefix: str = "2611") -> list[tuple[str, str, str]]:
    """n entries, newest first, one minute apart."""
    return [
        (f"{prefix}.{10000 + n - i:05d}v1", f"2026-11-{day:02d}T12:{(n - i) % 60:02d}:00Z", f"P{i}")
        for i in range(n)
    ]


def _fetch(
    upstream: ArxivFixtureUpstream,
    checkpoint: CheckpointState,
    config: dict[str, object] | None = None,
) -> tuple[list[dict[str, object]], CheckpointState, bool]:
    with make_arxiv_http(upstream) as http:
        page = ArxivFetcher().fetch_page(http, AI, checkpoint, config or {"max_results": 5})
    return [dict(r) for r in page.records], page.checkpoint, page.has_more


# --------------------------------------------------------------------------- policy & config


def test_official_endpoint_policy_and_rate_floor() -> None:
    assert API_URL == "https://export.arxiv.org/api/query"
    assert ArxivFetcher.host_policy.allowed_hosts == frozenset({"export.arxiv.org"})
    assert ArxivFetcher.host_policy.auth_hosts == frozenset()
    assert ArxivFetcher.allowed_namespaces == frozenset({ResourceNamespace.ARXIV_PAPER})
    f = ArxivFetcher()
    assert f.min_interval_ms({}) == 3100
    assert f.min_interval_ms({"min_interval_ms": 500}) == 3100  # cannot loosen arXiv's limit
    assert f.min_interval_ms({"min_interval_ms": 5000}) == 5000
    assert f.page_size({"max_results": 10_000}) == HARD_MAX_PAGE_SIZE


def test_streams_validate_categories_against_query_injection() -> None:
    f = ArxivFetcher()
    assert [s.name for s in f.streams({})] == ["cat:cs.AI", "cat:cs.CL", "cat:cs.LG", "cat:cs.SE"]
    streams = f.streams(
        {"categories": ["cs.AI", "stat.ML", "astro-ph.CO", "cs.AI OR all:x", "cs.*", "", "cs.AI"]}
    )
    assert [s.params["category"] for s in streams] == ["cs.AI", "stat.ML", "astro-ph.CO"]


def test_request_shape_matches_official_api() -> None:
    upstream = ArxivFixtureUpstream({"cs.AI": _entries(3)})
    _fetch(upstream, CheckpointState())
    req = upstream.requests[0]
    assert str(req.url).startswith("https://export.arxiv.org/api/query?")
    assert dict(req.url.params) == {
        "search_query": "cat:cs.AI",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": "0",
        "max_results": "5",
    }
    assert req.headers["accept"] == "application/atom+xml"


def test_rate_limiter_is_consulted_with_arxiv_spacing() -> None:
    calls: list[tuple[str, int]] = []

    class Recorder:
        def acquire(self, source_code: str, min_interval_ms: int) -> None:
            calls.append((source_code, min_interval_ms))

    upstream = ArxivFixtureUpstream({"cs.AI": _entries(3)})
    with make_arxiv_http(upstream, limiter=Recorder()) as http:
        ArxivFetcher().fetch_page(http, AI, CheckpointState(), {"max_results": 5})
    assert calls == [("ARXIV", 3100)]


# --------------------------------------------------------------------------- checkpoint & paging


def test_first_run_bootstraps_newest_page_only() -> None:
    upstream = ArxivFixtureUpstream({"cs.AI": _entries(12)})
    records, cp, has_more = _fetch(upstream, CheckpointState())
    assert len(records) == 5 and has_more is False  # no historical backfill
    assert upstream.starts_for("cs.AI") == [0]
    assert cp.cursor == {"next_start": 0, "high_water": "2026-11-12T12:12:00+00:00"}


def test_incremental_run_stops_at_high_water() -> None:
    upstream = ArxivFixtureUpstream({"cs.AI": _entries(12)})
    mark = CheckpointState(cursor={"high_water": "2026-11-12T12:09:00+00:00", "next_start": 0})
    records, cp, has_more = _fetch(upstream, mark)
    # newest 12..8 on the page; 12,11,10,9 are >= mark; 8 is older → cycle done
    assert [r["title"] for r in records] == ["P0", "P1", "P2", "P3"]
    assert has_more is False
    assert cp.cursor == {"next_start": 0, "high_water": "2026-11-12T12:12:00+00:00"}


def test_paging_continues_until_high_water_and_tracks_cycle_newest() -> None:
    upstream = ArxivFixtureUpstream({"cs.AI": _entries(12)})
    cp = CheckpointState(cursor={"high_water": "2026-11-12T12:01:00+00:00", "next_start": 0})
    records1, cp, more1 = _fetch(upstream, cp)
    assert more1 is True and len(records1) == 5
    assert cp.cursor == {
        "high_water": "2026-11-12T12:01:00+00:00",
        "next_start": 5,
        "cycle_newest": "2026-11-12T12:12:00+00:00",
    }
    records2, cp, more2 = _fetch(upstream, cp)
    assert more2 is True and len(records2) == 5
    records3, cp, more3 = _fetch(upstream, cp)
    assert more3 is False and [r["title"] for r in records3] == ["P10", "P11"]
    assert upstream.starts_for("cs.AI") == [0, 5, 10]
    assert cp.cursor == {"next_start": 0, "high_water": "2026-11-12T12:12:00+00:00"}


def test_deep_paging_is_capped() -> None:
    upstream = ArxivFixtureUpstream({"cs.AI": _entries(5)})
    cp = CheckpointState(
        cursor={
            "high_water": "2026-01-01T00:00:00+00:00",
            "next_start": MAX_OFFSET - 5,
            "cycle_newest": "2026-11-12T12:05:00+00:00",
        }
    )
    _records, new_cp, has_more = _fetch(upstream, cp)
    assert has_more is False
    assert new_cp.cursor == {"next_start": 0, "high_water": "2026-11-12T12:05:00+00:00"}


def test_empty_result_keeps_existing_high_water() -> None:
    upstream = ArxivFixtureUpstream({})
    upstream.raw_override["cs.AI"] = (ARXIV_FIXTURES / "empty.xml").read_bytes()
    mark = CheckpointState(cursor={"high_water": "2026-11-10T00:00:00+00:00", "next_start": 0})
    records, cp, has_more = _fetch(upstream, mark)
    assert records == [] and has_more is False
    assert cp.cursor == {"next_start": 0, "high_water": "2026-11-10T00:00:00+00:00"}


# --------------------------------------------------------------------------- parsing & security


def _from_fixture(name: str) -> list[dict[str, object]]:
    upstream = ArxivFixtureUpstream({})
    upstream.raw_override["cs.AI"] = (ARXIV_FIXTURES / name).read_bytes()
    records, _cp, _more = _fetch(upstream, CheckpointState(), {"max_results": 50})
    return records


def test_sample_feed_parses_and_never_requests_pdfs() -> None:
    upstream = ArxivFixtureUpstream({})
    upstream.raw_override["cs.AI"] = (ARXIV_FIXTURES / "cs_AI_sample.xml").read_bytes()
    records, _cp, _ = _fetch(upstream, CheckpointState(), {"max_results": 50})
    assert len(records) == 3
    assert all("/pdf/" not in str(r.url) for r in upstream.requests)
    first = records[0]
    assert first["title"] == "Fixture Paper Three: Planning with Small Language Models"
    assert first["authors_count"] == 2 and first["primary_category"] == "cs.AI"
    assert "published_dt" not in first  # internal field removed before records leave the fetcher


@pytest.mark.parametrize(
    "name",
    [
        "attack_billion_laughs.xml",
        "attack_external_entity.xml",
        "attack_dtd_only.xml",
        "not_atom.xml",
    ],
)
def test_xml_attacks_and_non_atom_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        _from_fixture(name)


def test_garbage_bytes_are_rejected() -> None:
    upstream = ArxivFixtureUpstream({})
    upstream.raw_override["cs.AI"] = b"\x00\xff<feed"
    with pytest.raises(ValueError):
        _fetch(upstream, CheckpointState())


def test_arxiv_api_error_feed_is_permanent() -> None:
    with pytest.raises(PermanentFetchError) as excinfo:
        _from_fixture("api_error.xml")
    assert excinfo.value.code == "ARXIV_API_ERROR"


def test_http_errors_propagate_for_runner_classification() -> None:
    upstream = ArxivFixtureUpstream({})
    upstream.status_override["cs.AI"] = 400
    with pytest.raises(PermanentFetchError):
        _fetch(upstream, CheckpointState())


# --------------------------------------------------------------------------- validate & normalise


def test_to_candidates_builds_abs_url_from_validated_id() -> None:
    records = _from_fixture("cs_AI_sample.xml")
    f = ArxivFetcher()
    (c,) = f.to_candidates(records[0])
    assert c.source_provider == ResourceNamespace.ARXIV_PAPER
    assert c.external_id == "2611.00003v1"
    assert c.raw_url == "https://arxiv.org/abs/2611.00003v1"
    assert c.version == "v1" and c.metadata["base_id"] == "2611.00003"
    assert c.metadata["authors_count"] == 2 and c.metadata["primary_category"] == "cs.AI"
    assert c.published_at == datetime(2026, 11, 12, 18, 0, tzinfo=UTC)
    summary = sanitize_minimal_summary(c.summary)
    assert summary is not None and "<b>" not in summary and "fixture planning" in summary
    assert "Fixture Author" not in repr(c)  # author names are not retained


def test_version_updates_share_a_topic_cluster() -> None:
    f = ArxivFetcher()
    v1, v2 = (
        f.to_candidates(
            {
                "id_url": f"http://arxiv.org/abs/2611.00002{v}",
                "title": "T",
                "published": "2026-11-12T12:00:00Z",
            }
        )[0]
        for v in ("v1", "v2")
    )
    assert v1.external_id != v2.external_id
    key = compute_dedupe_key(v1.source_provider, v1.external_id, v1.raw_url)
    assert key == compute_dedupe_key(v2.source_provider, v2.external_id, v2.raw_url)


def test_old_style_ids_are_supported() -> None:
    (c,) = ArxivFetcher().to_candidates(
        {
            "id_url": "http://arxiv.org/abs/cs/0112017v1",
            "title": "Old style",
            "published": "2001-12-11T00:00:00Z",
        }
    )
    assert c.raw_url == "https://arxiv.org/abs/cs/0112017v1"


def test_malformed_entries_raise_individually() -> None:
    records = _from_fixture("cs_CL_malformed_entries.xml")
    f = ArxivFetcher()
    outcomes: list[str] = []
    for r in records:
        try:
            f.to_candidates(r)
            outcomes.append("ok")
        except ValueError:
            outcomes.append("malformed")
    assert outcomes == ["ok", "malformed", "malformed", "malformed"]


@pytest.mark.parametrize(
    "id_url",
    [
        "http://arxiv.org/pdf/2611.00001v1",
        "https://arxiv.org.evil.example/abs/2611.00001v1",
        "http://arxiv.org/abs/2611.00001v1?x=1",
        "javascript:alert(1)",
        "",
    ],
)
def test_foreign_or_non_abs_ids_rejected(id_url: str) -> None:
    with pytest.raises(ValueError):
        ArxivFetcher().to_candidates(
            {"id_url": id_url, "title": "T", "published": "2026-11-12T12:00:00Z"}
        )


def test_bounded_config_narrows_to_one_category_and_n_items() -> None:
    f = ArxivFetcher()
    cfg = f.bounded_config({"categories": ["cs.AI", "cs.CL"], "max_results": 50}, AI, 5)
    assert cfg["categories"] == ["cs.AI"] and cfg["max_results"] == 5
    assert [s.name for s in f.streams(cfg)] == ["cat:cs.AI"] and f.page_size(cfg) == 5
