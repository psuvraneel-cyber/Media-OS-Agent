"""OpenAI News fetcher — official RSS feed https://openai.com/news/rss.xml.

Behaviour:
* Single stream (`news`). Conditional GET with the stored ETag / Last-Modified; a 304 costs one
  request and ingests nothing.
* RSS 2.0 parsed with `defusedxml` (DTDs, entities and external references forbidden).
* An HTML response (bot challenge or error page) is a permanent `FEED_BLOCKED_OR_HTML` failure;
  it is recorded in source health and **never circumvented** (no browser emulation or scraping).
* Items already processed are skipped via a bounded `seen_ids` window; at most `max_items` new items
  per run (default 30, hard cap 100).
* Links must be https URLs on openai.com; the item's stable id is the link path (e.g.
  `index/introducing-x`). The description is kept only as the sanitised ≤500-char discovery summary.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from media_os_api.research.connectors.openai_news import OpenAINewsConnector
from media_os_api.research.fetchers.base import FetchPage, SourceFetcher, StreamSpec
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.errors import PermanentFetchError
from media_os_api.research.http.policy import HostPolicy
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState

FEED_URL = "https://openai.com/news/rss.xml"
ALLOWED_LINK_HOSTS = frozenset({"openai.com", "www.openai.com"})
DEFAULT_MAX_ITEMS = 30
HARD_MAX_ITEMS = 100
SEEN_WINDOW = 500
MAX_TITLE_CHARS = 512


def _text(el: Any) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


class OpenAINewsFetcher(SourceFetcher):
    source_code: ClassVar[str] = "OPENAI_NEWS"
    host_policy: ClassVar[HostPolicy] = HostPolicy(
        allowed_hosts=frozenset({"openai.com", "www.openai.com"}),
        max_response_bytes=2 * 1024 * 1024,
    )
    allowed_namespaces: ClassVar[frozenset[str]] = frozenset({ResourceNamespace.OPENAI_NEWS})
    default_min_interval_ms: ClassVar[int] = 1000
    max_pages_per_stream: ClassVar[int] = 1

    def __init__(self) -> None:
        self._parser = OpenAINewsConnector()

    def streams(self, config: Mapping[str, Any]) -> list[StreamSpec]:
        return [StreamSpec(name="news")]

    @staticmethod
    def max_items(config: Mapping[str, Any]) -> int:
        try:
            value = int(config.get("max_items", DEFAULT_MAX_ITEMS))
        except (TypeError, ValueError):
            value = DEFAULT_MAX_ITEMS
        return max(1, min(value, HARD_MAX_ITEMS))

    def bounded_config(
        self, config: Mapping[str, Any], stream: StreamSpec, max_items: int
    ) -> dict[str, Any]:
        return {**config, "max_items": max_items}

    # ------------------------------------------------------------------ fetch

    def fetch_page(
        self,
        http: SafeHttpClient,
        stream: StreamSpec,
        checkpoint: CheckpointState,
        config: Mapping[str, Any],
    ) -> FetchPage:
        result = http.get(
            FEED_URL,
            etag=checkpoint.etag,
            last_modified=checkpoint.last_modified,
            accept="application/rss+xml, application/xml;q=0.9",
        )
        if result.not_modified:
            return FetchPage(
                records=[],
                checkpoint=CheckpointState(
                    cursor=dict(checkpoint.cursor),
                    etag=result.etag,
                    last_modified=result.last_modified,
                ),
                not_modified=True,
            )

        items = self._parse_feed(result.body, result.content_type)
        seen = [str(s) for s in checkpoint.cursor.get("seen_ids", []) if isinstance(s, str)]
        seen_set = set(seen)
        fresh = [i for i in items if i["item_key"] not in seen_set][: self.max_items(config)]
        new_keys = [str(i["item_key"]) for i in fresh if i["item_key"]]
        window = (new_keys + [s for s in seen if s not in set(new_keys)])[:SEEN_WINDOW]
        records: list[Mapping[str, Any]] = list(fresh)
        return FetchPage(
            records=records,
            checkpoint=CheckpointState(
                cursor={"seen_ids": window, "last_feed_items": len(items)},
                etag=result.etag,
                last_modified=result.last_modified,
            ),
        )

    def _parse_feed(self, body: bytes, content_type: str | None) -> list[dict[str, Any]]:
        head = body[:512].lstrip().lower()
        if (content_type and "html" in content_type.lower()) or head.startswith(
            (b"<!doctype html", b"<html")
        ):
            raise PermanentFetchError(
                "OpenAI News returned HTML instead of RSS (bot challenge or error page); "
                "not circumventing.",
                code="FEED_BLOCKED_OR_HTML",
            )
        try:
            root = SafeET.fromstring(
                body, forbid_dtd=True, forbid_entities=True, forbid_external=True
            )
        except (ParseError, DefusedXmlException) as exc:
            raise ValueError(
                f"OpenAI News feed is not safe/valid XML: {type(exc).__name__}"
            ) from exc
        channel = root.find("channel") if root.tag == "rss" else None
        if channel is None:
            raise ValueError("OpenAI News response is not an RSS 2.0 feed.")

        items: list[dict[str, Any]] = []
        for item in channel.findall("item"):
            link = _text(item.find("link"))
            items.append(
                {
                    "item_key": self._item_key(link) or _text(item.find("guid")),
                    "title": _text(item.find("title")),
                    "link": link,
                    "pub_date": _text(item.find("pubDate")),
                    "description": (item.findtext("description") or "").strip(),
                    "category": _text(item.find("category")) or None,
                }
            )
        return items

    @staticmethod
    def _item_key(link: str) -> str:
        try:
            parts = urlsplit(link)
        except ValueError:
            return ""
        if parts.scheme != "https" or parts.hostname not in ALLOWED_LINK_HOSTS:
            return ""
        return parts.path.strip("/")[:255]

    # ------------------------------------------------------------------ validate + normalise

    def to_candidates(self, record: Mapping[str, Any]) -> list[RawDiscoveryCandidate]:
        link = str(record.get("link") or "").strip()
        slug = self._item_key(link)
        if not slug:
            raise ValueError("Item link is not an https openai.com URL.")
        title = str(record.get("title") or "").strip()
        if not title:
            raise ValueError(f"OpenAI News item {slug} has no title.")
        try:
            published = parsedate_to_datetime(str(record.get("pub_date") or ""))
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"OpenAI News item {slug} has an invalid pubDate.") from exc
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        category = record.get("category")
        payload = {
            "slug": slug,
            "title": title[:MAX_TITLE_CHARS],
            "url": f"https://openai.com/{slug}",
            "published_at": published.astimezone(UTC),
            "summary": record.get("description") or None,
            "topic": category if isinstance(category, str) else None,
        }
        return [self._parser.parse_payload(payload)]
