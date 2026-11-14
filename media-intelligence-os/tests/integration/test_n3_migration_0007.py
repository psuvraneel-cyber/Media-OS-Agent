"""Integration (disposable DB): migration 0007 allows FACEBOOK and refuses an unsafe downgrade."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from media_os_api.db import create_db_engine

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


def _cfg() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "apps" / "api" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _db_url())
    return cfg


def _insert_channel(conn, platform: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    brand = conn.execute(text("SELECT id FROM brand_profiles LIMIT 1")).scalar_one()
    cid = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO channel_accounts (id, brand_profile_id, platform, external_account_id,"
            " status, created_at, updated_at) VALUES (:i, :b, :p, :e, 'CONNECTED', now(), now())"
        ),
        {"i": cid, "b": brand, "p": platform, "e": f"ext-{cid.hex[:8]}"},
    )
    return cid


def test_facebook_allowed_bad_platform_rejected_and_downgrade_guarded() -> None:
    engine = create_db_engine(_db_url(), pool_size=2, max_overflow=1)
    try:
        names = {str(c["name"]) for c in inspect(engine).get_check_constraints("channel_accounts")}
        assert "ck_channel_accounts_channel_platform_valid" in names
        assert not any(n.startswith("ck_channel_accounts_ck_") and "platform" in n for n in names)
        with engine.begin() as conn:
            fb = _insert_channel(conn, "FACEBOOK")
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _insert_channel(conn, "TIKTOK")
        with pytest.raises(RuntimeError, match="Refusing downgrade"):
            command.downgrade(_cfg(), "0006_content_tags_qa_score")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM channel_accounts WHERE id = :i"), {"i": fb})
        command.downgrade(_cfg(), "0006_content_tags_qa_score")
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _insert_channel(conn, "FACEBOOK")
        command.upgrade(_cfg(), "head")
    finally:
        engine.dispose()
