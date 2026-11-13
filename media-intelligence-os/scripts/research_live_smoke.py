"""Owner-gated, bounded LIVE smoke test for one research source — disposable test DB only.

This is the only tool that makes real outbound requests to a research source before scheduling is
enabled. It must only be run after the owner has explicitly approved a live check for that source.

What it does:
1. starts the disposable `media-os-test` stack (tmpfs Postgres/Redis) and migrates it to head;
2. in the *test* DB only, marks the source EXPERIMENTAL + approved and caps it to one stream and
   at most `--max-items` items (≤ 10);
3. runs the real fetcher through the real runner (SSRF policy, Redis limiter, retries, checkpoint);
4. prints a timestamped JSON evidence record (counts, health, sample of ingested titles/URLs);
5. stops the stack (all data discarded).

The runtime database, runtime Redis and Postiz are never contacted.

Usage:
    python scripts/research_live_smoke.py --source HACKERNEWS --owner-approved [--max-items 5]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_integration_tests import TEST_DB_URL, TEST_REDIS_URL, _compose  # noqa: E402

MAX_ITEMS_CAP = 10


def _run(cmd: list[str], env: dict[str, str] | None = None) -> int:
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=False).returncode


def _smoke(source: str, stream: str | None, max_items: int) -> dict[str, Any]:
    from sqlalchemy import select, update

    from media_os_api.db import (
        ResearchSource,
        SourceItem,
        create_db_engine,
        create_session_factory,
        session_scope,
    )
    from media_os_api.research.fetchers import FETCHERS
    from media_os_api.research.http import SafeHttpClient, build_redis_rate_limiter
    from media_os_api.research.runner import run_source_fetch

    fetcher_cls = FETCHERS[source]
    fetcher = fetcher_cls()
    engine = create_db_engine(TEST_DB_URL, pool_size=2, max_overflow=0)
    factory = create_session_factory(engine)
    started = datetime.now(UTC)
    try:
        with session_scope(factory) as db:
            row = db.get(ResearchSource, source)
            if row is None:
                raise SystemExit(f"Source {source} missing from test registry.")
            config = dict(row.config or {})
            streams = {s.name: s for s in fetcher.streams(config)}
            chosen = stream or next(iter(streams), "")
            if chosen not in streams:
                raise SystemExit(f"Stream '{chosen}' not valid for {source}: {sorted(streams)}")
            config = fetcher.bounded_config(config, streams[chosen], max_items)
            db.execute(
                update(ResearchSource)
                .where(ResearchSource.source_code == source)
                .values(
                    policy_mode="EXPERIMENTAL",
                    live_fetch_approved=True,
                    approved_by="owner:live-smoke-cli",
                    approved_at=started,
                    config=config,
                )
            )

        limiter = build_redis_rate_limiter(TEST_REDIS_URL)

        def http_factory(f: Any, cfg: Any) -> SafeHttpClient:
            return SafeHttpClient(
                source_code=f.source_code,
                policy=f.host_policy,
                rate_limiter=limiter,
                min_interval_ms=f.min_interval_ms(cfg),
            )

        outcome = run_source_fetch(factory, fetcher, http_factory=http_factory, env_enabled=True)
        with session_scope(factory) as db:
            src = db.get(ResearchSource, source)
            namespaces = sorted(fetcher.allowed_namespaces)
            sample = [
                {
                    "provider": i.source_provider,
                    "external_id": i.external_id,
                    "title": i.title[:140],
                    "canonical_url": i.canonical_url,
                    "published_at": i.published_at.isoformat() if i.published_at else None,
                    "summary_chars": len(i.minimal_permitted_summary or ""),
                }
                for i in db.execute(
                    select(SourceItem)
                    .where(SourceItem.source_provider.in_(namespaces))
                    .order_by(SourceItem.fetched_at.desc())
                    .limit(max_items)
                ).scalars()
            ]
            health = {
                "health_status": src.health_status if src else None,
                "last_error_code": src.last_error_code if src else None,
            }
        return {
            "live_smoke": True,
            "database": "disposable media-os-test (tmpfs) — runtime DB untouched",
            "source": source,
            "stream": chosen,
            "max_items": max_items,
            "started_at_utc": started.isoformat(),
            "finished_at_utc": datetime.now(UTC).isoformat(),
            "outcome": outcome.as_dict(),
            "health": health,
            "sample_items": sample,
        }
    finally:
        engine.dispose()


def main() -> int:
    from media_os_api.research.fetchers import FETCHERS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=sorted(FETCHERS))
    parser.add_argument("--stream", default=None)
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument(
        "--owner-approved",
        action="store_true",
        help="Required: confirms the owner explicitly approved this live check.",
    )
    parser.add_argument("--keep", action="store_true", help="Leave the test stack running.")
    args = parser.parse_args()

    if not args.owner_approved:
        print(
            "[REFUSED] Live fetches require --owner-approved (explicit owner approval).",
            file=sys.stderr,
        )
        return 2
    if args.source == "REDDIT":
        print("[REFUSED] Reddit is BLOCKED by policy.", file=sys.stderr)
        return 2
    max_items = max(1, min(args.max_items, MAX_ITEMS_CAP))

    if _run(_compose("up", "-d", "--wait")) != 0:
        print("[SMOKE-FAIL] could not start disposable test stack", file=sys.stderr)
        return 1
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL"}
    }
    env["DATABASE_URL"] = TEST_DB_URL
    try:
        if _run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env) != 0:
            print("[SMOKE-FAIL] alembic upgrade failed on disposable DB", file=sys.stderr)
            return 1
        try:
            evidence = _smoke(args.source, args.stream, max_items)
        except Exception as exc:  # noqa: BLE001 - report the failure as evidence, then clean up
            evidence = {
                "live_smoke": True,
                "source": args.source,
                "error_type": type(exc).__name__,
                "error_code": getattr(exc, "code", None),
                "error": str(exc)[:300],
                "at_utc": datetime.now(UTC).isoformat(),
            }
            print(json.dumps(evidence, indent=2))
            return 1
        print(json.dumps(evidence, indent=2))
        return 0
    finally:
        if not args.keep:
            _run(_compose("down"))


if __name__ == "__main__":
    raise SystemExit(main())
