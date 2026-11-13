"""arXiv fetcher — official arXiv API (Atom), https://info.arxiv.org/help/api/

Endpoint (public, no key):
  GET https://export.arxiv.org/api/query
      ?search_query=cat:<category>&sortBy=submittedDate&sortOrder=descending
      &start=<offset>&max_results=<n>

Behaviour:
* One stream per configured category (default cs.AI, cs.CL, cs.LG, cs.SE). Category strings are
  validated against a strict pattern so config cannot inject query operators.
* ≥ 3.1 s between requests (arXiv asks for one request every 3 s); registry config may only tighten.
* Metadata only: the abstract link is constructed from the validated paper id; PDF links in the
  feed are ignored and never requested. Author *names* are not kept — only a count.
* Checkpoint per category = high-water mark of the newest `published` timestamp already ingested.
  - first run (no high-water): ingest the newest page only (no historical backfill);
  - later runs: page newest-first until an entry at/below the high-water mark or the end of results
    (bounded by `max_pages_per_stream` and a hard offset cap), then advance the mark.
* Atom is parsed with `defusedxml` (DTDs/entities rejected). An arXiv API error feed raises a
  permanent `ARXIV_API_ERROR`; unparseable XML raises `ValueError` → `MALFORMED_DOCUMENT`.
* arXiv's API does not provide ETag/Last-Modified validators; the high-water mark is the
  incremental mechanism.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from media_os_api.research.connectors.arxiv import ArxivConnector
from media_os_api.research.fetchers.base import FetchPage, SourceFetcher, StreamSpec
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.errors import PermanentFetchError
from media_os_api.research.http.policy import HostPolicy
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState

API_URL = "https://export.arxiv.org/api/query"
DEFAULT_CATEGORIES = ("cs.AI", "cs.CL", "cs.LG", "cs.SE")
DEFAULT_PAGE_SIZE = 50
HARD_MAX_PAGE_SIZE = 100
MAX_OFFSET = 500  # never page deeper than this within one cycle

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}
_CATEGORY_PATTERN = re.compile(r"^[a-z]+(?:-[a-z]+)?\.[A-Za-z]{2,4}$")
_ID_URL_PATTERN = re.compile(
    r"^https?://arxiv\.org/abs/"
    r"(?P<id>(?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)$"
)
MAX_TITLE_CHARS = 512


def _text(el: Element | None) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


def _parse_ts(value: str) -> datetime:
    """Parse an Atom timestamp; raises ValueError if absent or invalid."""
    if not value:
        raise ValueError("missing timestamp")
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


class ArxivFetcher(SourceFetcher):
    source_code: ClassVar[str] = "ARXIV"
    host_policy: ClassVar[HostPolicy] = HostPolicy(
        allowed_hosts=frozenset({"export.arxiv.org"}),
        max_response_bytes=2 * 1024 * 1024,
        read_timeout_seconds=30.0,  # arXiv API responses can be slow
    )
    allowed_namespaces: ClassVar[frozenset[str]] = frozenset({ResourceNamespace.ARXIV_PAPER})
    default_min_interval_ms: ClassVar[int] = 3100
    max_pages_per_stream: ClassVar[int] = 3

    def __init__(self) -> None:
        self._parser = ArxivConnector()

    # ------------------------------------------------------------------ configuration

    def streams(self, config: Mapping[str, Any]) -> list[StreamSpec]:
        configured = config.get("categories") or list(DEFAULT_CATEGORIES)
        valid = [str(c) for c in configured if _CATEGORY_PATTERN.match(str(c))]
        return [StreamSpec(name=f"cat:{c}", params={"category": c}) for c in dict.fromkeys(valid)]

    def bounded_config(
        self, config: Mapping[str, Any], stream: StreamSpec, max_items: int
    ) -> dict[str, Any]:
        return {**config, "categories": [stream.params["category"]], "max_results": max_items}

    @staticmethod
    def page_size(config: Mapping[str, Any]) -> int:
        try:
            value = int(config.get("max_results", DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError):
            value = DEFAULT_PAGE_SIZE
        return max(1, min(value, HARD_MAX_PAGE_SIZE))

    # ------------------------------------------------------------------ fetch

    def fetch_page(
        self,
        http: SafeHttpClient,
        stream: StreamSpec,
        checkpoint: CheckpointState,
        config: Mapping[str, Any],
    ) -> FetchPage:
        category = str(stream.params["category"])
        size = self.page_size(config)
        cursor = dict(checkpoint.cursor)
        high_water_raw = cursor.get("high_water")
        high_water = _parse_ts(high_water_raw) if isinstance(high_water_raw, str) else None
        start = int(cursor.get("next_start", 0)) if high_water is not None else 0
        cycle_newest_raw = cursor.get("cycle_newest") if start > 0 else None

        result = http.get(
            API_URL,
            params={
                "search_query": f"cat:{category}",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "start": start,
                "max_results": size,
            },
            accept="application/atom+xml",
        )
        entries = self._parse_feed(result.body)

        published = [e["published_dt"] for e in entries if e.get("published_dt") is not None]
        page_newest = max(published) if published else None
        cycle_newest = page_newest
        if isinstance(cycle_newest_raw, str):
            prev = _parse_ts(cycle_newest_raw)
            cycle_newest = max(prev, page_newest) if page_newest else prev

        if high_water is None:
            records = entries
            reached_known = True  # bootstrap: newest page only, no backfill
        else:
            records = [
                e
                for e in entries
                if e.get("published_dt") is None or e["published_dt"] >= high_water
            ]
            reached_known = any(
                e.get("published_dt") is not None and e["published_dt"] < high_water
                for e in entries
            )
        end_of_results = len(entries) < size
        next_start = start + size
        cycle_done = reached_known or end_of_results or next_start >= MAX_OFFSET

        if cycle_done:
            new_mark = high_water
            if cycle_newest is not None and (new_mark is None or cycle_newest > new_mark):
                new_mark = cycle_newest
            new_cursor: dict[str, Any] = {"next_start": 0}
            if new_mark is not None:
                new_cursor["high_water"] = new_mark.isoformat()
        else:
            new_cursor = {
                "high_water": high_water.isoformat() if high_water else None,
                "next_start": next_start,
                "cycle_newest": cycle_newest.isoformat() if cycle_newest else None,
            }

        for e in records:
            e.pop("published_dt", None)
        return FetchPage(
            records=list(records),
            checkpoint=CheckpointState(cursor=new_cursor),
            has_more=not cycle_done,
        )

    def _parse_feed(self, body: bytes) -> list[dict[str, Any]]:
        try:
            # Atom never needs a DTD: forbid DTDs outright, plus entities and external references.
            root = SafeET.fromstring(
                body, forbid_dtd=True, forbid_entities=True, forbid_external=True
            )
        except (ParseError, DefusedXmlException) as exc:
            raise ValueError(
                f"arXiv response is not safe/valid Atom XML: {type(exc).__name__}"
            ) from exc
        if root.tag != f"{{{_NS['atom']}}}feed":
            raise ValueError("arXiv response is not an Atom feed.")

        entries: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", _NS):
            id_text = _text(entry.find("atom:id", _NS))
            title = _text(entry.find("atom:title", _NS))
            if "/api/errors" in id_text or (title == "Error" and "arxiv.org/api" in id_text):
                detail = _text(entry.find("atom:summary", _NS))[:200]
                raise PermanentFetchError(f"arXiv API error: {detail}", code="ARXIV_API_ERROR")
            published_text = _text(entry.find("atom:published", _NS))
            try:
                published_dt: datetime | None = _parse_ts(published_text)
            except ValueError:
                published_dt = None  # validated (and rejected) per record in to_candidates
            primary = entry.find("arxiv:primary_category", _NS)
            entries.append(
                {
                    "id_url": id_text,
                    "title": title,
                    "summary": _text(entry.find("atom:summary", _NS)),
                    "published": published_text,
                    "published_dt": published_dt,
                    "updated": _text(entry.find("atom:updated", _NS)),
                    "authors_count": len(entry.findall("atom:author", _NS)),
                    "primary_category": primary.get("term") if primary is not None else None,
                }
            )
        return entries

    # ------------------------------------------------------------------ validate + normalise

    def to_candidates(self, record: Mapping[str, Any]) -> list[RawDiscoveryCandidate]:
        match = _ID_URL_PATTERN.match(str(record.get("id_url") or "").strip())
        if not match:
            raise ValueError("Entry id is not an arxiv.org/abs/<paper-id> URL.")
        paper_id = match.group("id")
        title = str(record.get("title") or "").strip()
        if not title:
            raise ValueError(f"arXiv entry {paper_id} has no title.")
        published = _parse_ts(str(record.get("published") or ""))
        primary = record.get("primary_category")
        payload = {
            "id": paper_id,
            "title": title[:MAX_TITLE_CHARS],
            "url": f"https://arxiv.org/abs/{paper_id}",  # built from the validated id only
            "published_at": published,
            "summary": record.get("summary") or None,
            "authors_count": int(record.get("authors_count") or 0),
            "primary_category": primary if isinstance(primary, str) else None,
            "category": "AI Research",
        }
        return [self._parser.parse_payload(payload)]
