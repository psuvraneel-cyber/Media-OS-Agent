"""GitHub fetcher — official REST API (https://docs.github.com/rest), read-only public metadata.

Streams:
* `search:<query>` — one per configured query:
  `GET https://api.github.com/search/repositories?q=<query> pushed:>YYYY-MM-DD&sort=stars&order=desc
  &per_page=<n>`; recently active, well-starred public repositories.
* `releases` — the owner's watch-list: `GET https://api.github.com/repos/{owner}/{repo}/releases
  ?per_page=<n>` with a per-repository ETag (unchanged repos answer 304).

Behaviour:
* Authentication is optional. A read-only token (env `GITHUB_TOKEN`, injected by the job layer) is
  only ever sent to `api.github.com`; it is never stored, logged or placed in payloads. Without a
  token, GitHub allows ~10 search requests/min and 60 core requests/hour, hence ≥ 6.5 s spacing.
* Rate-limit responses (403/429 with `X-RateLimit-Remaining: 0` or `Retry-After`) become retryable
  errors whose wait feeds the per-source backoff.
* Search queries are validated against a conservative character set; the fetcher adds the `pushed:`
  window itself (default last 7 days).
* Forks, archived, disabled and private repositories are filtered (not malformed). Draft releases
  are never returned unauthenticated; pre-releases are skipped unless `include_prereleases` is set.
* URLs are rebuilt from validated `owner/repo` and tag names; repository archives and assets are
  never downloaded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import quote

from media_os_api.research.connectors.github import GitHubConnector
from media_os_api.research.fetchers.base import FetchPage, SourceFetcher, StreamSpec
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.errors import PermanentFetchError, RetryableFetchError
from media_os_api.research.http.policy import HostPolicy
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState

API_BASE = "https://api.github.com"
GITHUB_HEADERS_ACCEPT = "application/vnd.github+json"
_QUERY_PATTERN = re.compile(r"^[A-Za-z0-9 :._+\-]{1,100}$")
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9._\-+/]{1,128}$")
DEFAULT_PER_QUERY = 30
HARD_MAX_PER_QUERY = 50
DEFAULT_LOOKBACK_DAYS = 7
MAX_WATCHED = 20
DEFAULT_RELEASES_PER_REPO = 5
SEEN_WINDOW = 1000


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(v, hi))


def _json(body: bytes, what: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"GitHub {what} response is not valid JSON.") from exc


class GitHubFetcher(SourceFetcher):
    source_code: ClassVar[str] = "GITHUB"
    host_policy: ClassVar[HostPolicy] = HostPolicy(
        allowed_hosts=frozenset({"api.github.com"}),
        auth_hosts=frozenset({"api.github.com"}),
        max_response_bytes=2 * 1024 * 1024,
    )
    allowed_namespaces: ClassVar[frozenset[str]] = frozenset(
        {ResourceNamespace.GITHUB_REPOSITORY, ResourceNamespace.GITHUB_RELEASE}
    )
    default_min_interval_ms: ClassVar[int] = 6500
    max_pages_per_stream: ClassVar[int] = 1

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._repo_parser = GitHubConnector(resource_type=ResourceNamespace.GITHUB_REPOSITORY)
        self._release_parser = GitHubConnector(resource_type=ResourceNamespace.GITHUB_RELEASE)

    # ------------------------------------------------------------------ configuration

    def streams(self, config: Mapping[str, Any]) -> list[StreamSpec]:
        out: list[StreamSpec] = []
        for q in dict.fromkeys(str(q).strip() for q in config.get("search_queries") or []):
            if _QUERY_PATTERN.match(q) and "pushed:" not in q:
                out.append(StreamSpec(name=f"search:{q}"[:128], params={"query": q}))
        if self.watched(config):
            out.append(StreamSpec(name="releases"))
        return out

    @staticmethod
    def watched(config: Mapping[str, Any]) -> list[str]:
        repos = [str(r).strip() for r in config.get("watched_repositories") or []]
        return [r for r in dict.fromkeys(repos) if _REPO_PATTERN.match(r)][:MAX_WATCHED]

    def bounded_config(
        self, config: Mapping[str, Any], stream: StreamSpec, max_items: int
    ) -> dict[str, Any]:
        if stream.name == "releases":
            return {
                **config,
                "search_queries": [],
                "watched_repositories": self.watched(config)[:1],
                "max_releases_per_repository": max_items,
            }
        return {
            **config,
            "search_queries": [stream.params["query"]],
            "watched_repositories": [],
            "max_repositories_per_query": max_items,
        }

    # ------------------------------------------------------------------ fetch

    def fetch_page(
        self,
        http: SafeHttpClient,
        stream: StreamSpec,
        checkpoint: CheckpointState,
        config: Mapping[str, Any],
    ) -> FetchPage:
        if stream.name == "releases":
            return self._fetch_releases(http, checkpoint, config)
        return self._fetch_search(http, str(stream.params["query"]), checkpoint, config)

    def _fetch_search(
        self,
        http: SafeHttpClient,
        query: str,
        checkpoint: CheckpointState,
        config: Mapping[str, Any],
    ) -> FetchPage:
        per = _int(
            config.get("max_repositories_per_query"), DEFAULT_PER_QUERY, 1, HARD_MAX_PER_QUERY
        )
        lookback = _int(config.get("lookback_days"), DEFAULT_LOOKBACK_DAYS, 1, 30)
        since = (self._now() - timedelta(days=lookback)).date().isoformat()
        result = http.get(
            f"{API_BASE}/search/repositories",
            params={
                "q": f"{query} pushed:>{since}",
                "sort": "stars",
                "order": "desc",
                "per_page": per,
            },
            accept=GITHUB_HEADERS_ACCEPT,
        )
        doc = _json(result.body, "search")
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
            raise ValueError("GitHub search response has no items array.")
        seen = [s for s in checkpoint.cursor.get("seen", []) if isinstance(s, str)]
        seen_set = set(seen)
        records: list[Mapping[str, Any]] = []
        new_names: list[str] = []
        for item in doc["items"][:per]:
            if not isinstance(item, dict):
                records.append({"kind": "repo"})  # surfaces as malformed in to_candidates
                continue
            name = str(item.get("full_name") or "").lower()
            if name and name in seen_set:
                continue
            records.append({"kind": "repo", **item})
            if name:
                new_names.append(name)
        window = (new_names + [s for s in seen if s not in set(new_names)])[:SEEN_WINDOW]
        return FetchPage(
            records=records,
            checkpoint=CheckpointState(cursor={"seen": window, "pushed_since": since}),
        )

    def _fetch_releases(
        self, http: SafeHttpClient, checkpoint: CheckpointState, config: Mapping[str, Any]
    ) -> FetchPage:
        per = _int(config.get("max_releases_per_repository"), DEFAULT_RELEASES_PER_REPO, 1, 20)
        include_pre = bool(config.get("include_prereleases", False))
        etags = {
            k: v
            for k, v in dict(checkpoint.cursor.get("etags", {})).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        seen = [s for s in checkpoint.cursor.get("seen_releases", []) if isinstance(s, str)]
        seen_set = set(seen)
        records: list[Mapping[str, Any]] = []
        new_seen: list[str] = []
        errors: dict[str, int] = {}
        first_error: Exception | None = None
        repos = self.watched(config)

        for repo in repos:
            owner, name = repo.split("/", 1)
            try:
                result = http.get(
                    f"{API_BASE}/repos/{quote(owner)}/{quote(name)}/releases",
                    params={"per_page": per},
                    etag=etags.get(repo),
                    accept=GITHUB_HEADERS_ACCEPT,
                )
                if result.not_modified:
                    continue
                doc = _json(result.body, "releases")
                if not isinstance(doc, list):
                    raise PermanentFetchError(
                        f"Releases for {repo} not a list.", code="MALFORMED_RELEASES"
                    )
            except (RetryableFetchError, PermanentFetchError) as exc:
                errors[exc.code] = errors.get(exc.code, 0) + 1
                first_error = first_error or exc
                continue
            except ValueError as exc:
                errors["MALFORMED_RELEASES"] = errors.get("MALFORMED_RELEASES", 0) + 1
                first_error = first_error or exc
                continue
            if result.etag:
                etags[repo] = result.etag
            for rel in doc[:per]:
                if not isinstance(rel, dict):
                    continue
                if rel.get("draft") or (rel.get("prerelease") and not include_pre):
                    continue
                key = f"{repo.lower()}:{rel.get('tag_name')}"
                if key in seen_set:
                    continue
                records.append({"kind": "release", "full_name": repo, **rel})
                new_seen.append(key)

        if repos and first_error is not None and sum(errors.values()) == len(repos):
            raise first_error
        window = (new_seen + [s for s in seen if s not in set(new_seen)])[:SEEN_WINDOW]
        return FetchPage(
            records=records,
            checkpoint=CheckpointState(cursor={"etags": etags, "seen_releases": window}),
            fetch_errors=errors,
        )

    # ------------------------------------------------------------------ validate + normalise

    def to_candidates(self, record: Mapping[str, Any]) -> list[RawDiscoveryCandidate]:
        if record.get("kind") == "release":
            return self._release_candidate(record)
        return self._repo_candidate(record)

    def _repo_candidate(self, r: Mapping[str, Any]) -> list[RawDiscoveryCandidate]:
        full_name = str(r.get("full_name") or "")
        if not _REPO_PATTERN.match(full_name):
            raise ValueError("Repository full_name is missing or invalid.")
        if r.get("private") or r.get("fork") or r.get("archived") or r.get("disabled"):
            return []  # filtered, not malformed
        created = r.get("created_at")
        if not isinstance(created, str):
            raise ValueError(f"Repository {full_name} has no created_at.")
        lic = r.get("license")
        spdx = lic.get("spdx_id") if isinstance(lic, dict) else None
        desc = r.get("description")
        payload = {
            "full_name": full_name,
            "html_url": f"https://github.com/{full_name}",
            "description": desc.strip()[:300]
            if isinstance(desc, str) and desc.strip()
            else "Repository",
            "created_at": created,
            "stargazers_count": r.get("stargazers_count")
            if isinstance(r.get("stargazers_count"), int)
            else 0,
            "language": r.get("language") if isinstance(r.get("language"), str) else None,
            "license": spdx if isinstance(spdx, str) else None,
            "category": "Open Source",
        }
        return [self._repo_parser.parse_payload(payload)]

    def _release_candidate(self, r: Mapping[str, Any]) -> list[RawDiscoveryCandidate]:
        full_name = str(r.get("full_name") or "")
        tag = str(r.get("tag_name") or "")
        if not _REPO_PATTERN.match(full_name) or not _TAG_PATTERN.match(tag):
            raise ValueError("Release has an invalid repository or tag name.")
        published = r.get("published_at")
        if not isinstance(published, str):
            raise ValueError(f"Release {full_name}@{tag} has no published_at.")
        name = r.get("name")
        body = r.get("body")
        payload = {
            "full_name": full_name,
            "tag_name": tag,
            "html_url": f"https://github.com/{full_name}/releases/tag/{quote(tag, safe='')}",
            "name": f"{full_name} {name.strip()}"[:300]
            if isinstance(name, str) and name.strip()
            else f"{full_name} {tag}",
            "published_at": published,
            "body": body if isinstance(body, str) else None,
            "category": "Developer Tools",
        }
        return [self._release_parser.parse_payload(payload)]
