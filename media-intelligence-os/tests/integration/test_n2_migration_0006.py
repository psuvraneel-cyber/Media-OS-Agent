"""Integration (disposable DB): migration 0006 adds JSON-object columns to content packages."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from media_os_api.db import check_database_health, create_db_engine

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
HEAD = "0015_design_templates"  # current head; 0006 is checked below


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


def test_columns_constraints_and_round_trip() -> None:
    engine = create_db_engine(_db_url(), pool_size=2, max_overflow=1)
    try:
        ok, _detail, ver = check_database_health(engine)
        assert ok and ver == HEAD
        cols = {c["name"]: c for c in inspect(engine).get_columns("content_packages")}
        for name in ("post_tags", "qa_report", "score"):
            assert cols[name]["nullable"] is True
        checks = {c["name"] for c in inspect(engine).get_check_constraints("content_packages")}
        assert {
            "ck_content_packages_post_tags_is_object",
            "ck_content_packages_qa_report_is_object",
            "ck_content_packages_score_is_object",
        } <= checks

        with pytest.raises(IntegrityError), engine.begin() as conn:
            brand = conn.execute(text("SELECT id FROM brand_profiles LIMIT 1")).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO content_packages (id, brand_profile_id, platform, format, status,"
                    " approval_status, version, hashtags, slide_data, source_manifest, post_tags,"
                    " created_at, updated_at) VALUES (gen_random_uuid(), :b, 'INSTAGRAM',"
                    " 'CAROUSEL', 'DRAFTED', 'PENDING', 1, '[]', '[]', '[]', '[1,2]', now(), now())"
                ),
                {"b": brand},
            )

        command.downgrade(_cfg(), "0005_research_source_registry")
        assert "post_tags" not in {
            c["name"] for c in inspect(engine).get_columns("content_packages")
        }
        command.upgrade(_cfg(), "head")
        assert check_database_health(engine)[2] == HEAD
    finally:
        engine.dispose()
