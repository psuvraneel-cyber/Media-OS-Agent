"""Owner CLI for carousel generation (runs in the worker, which holds the AI keys).

    python -m media_os_api.carousel.admin draft --pillar wedding_season --hook SEASONAL \\
        --occasion WEDDING_HINDU --language BN_EN          # preview only, nothing saved
    python -m media_os_api.carousel.admin generate ...     # save as a DRAFTED content package

Both make live AI calls (free tiers). Nothing is published.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from media_os_api.ai.config import build_router
from media_os_api.ai.quota import InMemoryQuotaStore, QuotaStore, RedisQuotaStore
from media_os_api.carousel.brand_kit import kit_for
from media_os_api.carousel.schema import CarouselBrief
from media_os_api.carousel.score import ai_review, score_carousel
from media_os_api.carousel.writer import write_with_qa
from media_os_api.config import load_settings
from media_os_api.content.taxonomy import (
    CallToAction,
    HookType,
    Language,
    Occasion,
    PostingSlot,
    VisualStyle,
)


def _quota_store() -> QuotaStore:
    settings = load_settings()
    if settings.redis_url is None:
        return InMemoryQuotaStore()
    from redis import Redis

    return RedisQuotaStore(
        Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=2.0, socket_connect_timeout=2.0
        )
    )


def produce(brand_slug: str, brief: CarouselBrief, *, save: bool) -> dict[str, Any]:
    kit = kit_for(brand_slug)
    settings = load_settings()
    on_attempt = None
    factory = None
    if save:
        from media_os_api.ai.usage import usage_recorder
        from media_os_api.db.session import (
            create_db_engine_from_settings,
            create_session_factory,
        )

        factory = create_session_factory(create_db_engine_from_settings(settings))
        on_attempt = usage_recorder(factory)
    router = build_router(quota_store=_quota_store(), on_attempt=on_attempt)
    try:
        draft, written_by, attempts, qa = write_with_qa(router, kit, brief)
        score = score_carousel(brief, draft, ai_review(router, draft))
    finally:
        router.close()
    out: dict[str, Any] = {
        "written_by": written_by,
        "attempts": [f"{a.provider}/{a.model}={a.outcome}" for a in attempts],
        "qa": qa.to_json(),
        "score": score,
        "draft": draft.model_dump(mode="json"),
    }
    if save and factory is not None:
        from media_os_api.carousel.service import create_carousel_package
        from media_os_api.db.session import session_scope

        with session_scope(factory) as db:
            pkg = create_carousel_package(
                db,
                brand_slug=brand_slug,
                brief=brief,
                draft=draft,
                qa=qa,
                score=score,
                written_by=written_by,
            )
            out["content_package_id"] = str(pkg.id)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="media_os_api.carousel.admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("draft", "generate"):
        p = sub.add_parser(name)
        p.add_argument("--brand", default="brand-app-promo")
        p.add_argument("--pillar", required=True)
        p.add_argument("--hook", required=True, choices=[h.value for h in HookType])
        p.add_argument("--occasion", default="GENERAL", choices=[o.value for o in Occasion])
        p.add_argument("--language", required=True, choices=[lang.value for lang in Language])
        p.add_argument(
            "--visual", default="BRAND_TYPOGRAPHY", choices=[v.value for v in VisualStyle]
        )
        p.add_argument("--cta", default="INSTALL", choices=[c.value for c in CallToAction])
        p.add_argument("--slot", default="EVENING", choices=[s.value for s in PostingSlot])
        p.add_argument("--angle", default=None)
    args = parser.parse_args(argv)
    brief = CarouselBrief(
        pillar=args.pillar,
        hook_type=HookType(args.hook),
        occasion=Occasion(args.occasion),
        language=Language(args.language),
        visual_style=VisualStyle(args.visual),
        call_to_action=CallToAction(args.cta),
        posting_slot=PostingSlot(args.slot),
        angle=args.angle,
    )
    result = produce(args.brand, brief, save=args.cmd == "generate")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
