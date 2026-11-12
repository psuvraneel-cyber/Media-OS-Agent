"""Unit tests for Phase 4 settings, normalization, state machine, jobs, DST, and fallback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from media_os_api.auth import (
    LoginRateLimiter,
    RateLimiterUnavailableError,
    RedisLoginRateLimiter,
    hash_password,
)
from media_os_api.config import ConfigurationError, load_settings
from media_os_api.db.normalization import compute_content_hash, normalize_canonical_url
from media_os_api.db.retention import RetentionPolicyRegistry
from media_os_api.jobs import (
    CELERY_QUEUES,
    AuthenticationTaskError,
    JobPayloadSecurityError,
    PermanentTaskError,
    PolicyRefusalError,
    RetryableTaskError,
    build_celery_app,
    classify_exception,
    compute_backoff_seconds,
    compute_next_run_at,
    validate_safe_task_payload,
)
from media_os_api.observability.redaction import redact_mapping, redact_text
from media_os_api.workflow import (
    PRIMARY_PIPELINE_ORDER,
    ContentLifecycleStatus,
    allowed_targets_for,
    is_transition_allowed,
)

pytestmark = pytest.mark.unit


def _sample_hash() -> str:
    return hash_password("Phase4-Strong-Test-Pass!2026")


def test_phase4_settings_fail_closed_in_production_and_validate_dsns() -> None:
    pwd_hash = _sample_hash()
    secret = "0123456789abcdef0123456789abcdef0123456789abcdef"

    # Production requires DB + Redis DSNs fail-closed
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        load_settings(
            {
                "APP_ENV": "production",
                "OWNER_USERNAME": "owner",
                "OWNER_PASSWORD_HASH": pwd_hash,
                "SESSION_SECRET": secret,
                "COOKIE_SECURE": "false",
            }
        )

    # Placeholder password inside DATABASE_URL or REDIS_URL fails closed
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        load_settings(
            {
                "OWNER_USERNAME": "owner",
                "OWNER_PASSWORD_HASH": pwd_hash,
                "SESSION_SECRET": secret,
                "DATABASE_URL": "postgresql+psycopg://u:REPLACE_WITH_LOCAL_POSTGRES_PASSWORD@db:5432/media_os",
            }
        )

    with pytest.raises(ConfigurationError, match="Unknown IANA timezone"):
        load_settings(
            {
                "OWNER_USERNAME": "owner",
                "OWNER_PASSWORD_HASH": pwd_hash,
                "SESSION_SECRET": secret,
                "APP_DISPLAY_TIMEZONE": "Mars/Olympus_Mons",
            }
        )

    valid = load_settings(
        {
            "APP_ENV": "production",
            "OWNER_USERNAME": "owner",
            "OWNER_PASSWORD_HASH": pwd_hash,
            "SESSION_SECRET": secret,
            "COOKIE_SECURE": "false",
            "POSTGRES_PASSWORD": "pg-strong-random-password-xyz",
            "REDIS_PASSWORD": "redis-strong-random-password-xyz",
            "APP_DISPLAY_TIMEZONE": "Asia/Kolkata",
        }
    )
    assert valid.persistence_enabled is True
    assert "pg-strong-random-password-xyz" in (valid.resolved_database_url() or "")
    assert "redis-strong-random-password-xyz" in (valid.resolved_redis_url() or "")


def test_dsn_redaction_strips_postgres_and_redis_passwords() -> None:
    pg = "postgresql+psycopg://media_os_user:SuperSecretPgPass123@media-os-postgres:5432/media_os"
    rd = "redis://:SuperSecretRedisPass456@media-os-redis:6379/0"
    scrubbed_text = redact_text(f"Connect failed: {pg} and {rd}")
    assert "SuperSecretPgPass123" not in scrubbed_text
    assert "SuperSecretRedisPass456" not in scrubbed_text
    assert "[REDACTED]@media-os-postgres:5432/media_os" in scrubbed_text

    scrubbed_map = redact_mapping({"database_url": pg, "redis_url": rd, "ok": "yes"})
    assert scrubbed_map["database_url"] == "[REDACTED]"
    assert scrubbed_map["redis_url"] == "[REDACTED]"
    assert scrubbed_map["ok"] == "yes"


def test_canonical_url_normalization_and_hashing_are_deterministic() -> None:
    u1 = normalize_canonical_url(
        "HTTPS://Example.COM:443/blog/post/?utm_source=hn&b=2&a=1&fbclid=xyz#section"
    )
    u2 = normalize_canonical_url("https://example.com/blog/post?a=1&b=2")
    assert u1 == u2 == "https://example.com/blog/post?a=1&b=2"

    h1 = compute_content_hash("Hello   world\n\nfrom   Media OS")
    h2 = compute_content_hash("Hello world from Media OS")
    assert h1 == h2 and len(h1) == 64


def test_retention_policy_registry_enforces_youtube_30_day_limits() -> None:
    registry = RetentionPolicyRegistry()
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

    hn_exp = registry.compute_source_expiry(source_provider="HACKER_NEWS", fetched_at=now)
    assert hn_exp == now + timedelta(days=365)

    # Custom days cannot extend any YouTube source provider variant past 30 days
    for yt_provider in ("YOUTUBE", "YOUTUBE_API", "YOUTUBE_PUBLIC", "youtube_data_v3"):
        yt_src_exp = registry.compute_source_expiry(
            source_provider=yt_provider,
            fetched_at=now,
            custom_days=180,
        )
        assert yt_src_exp == now + timedelta(days=30)

    # Blocked Reddit sources are rejected by policy
    with pytest.raises(ValueError, match="BLOCKED"):
        registry.compute_source_expiry(source_provider="REDDIT", fetched_at=now)

    yt_non_auth = registry.compute_youtube_metrics_expiry(
        authorization_class="NON_AUTHORIZED_PUBLIC",
        fetched_at=now,
    )
    assert yt_non_auth == now + timedelta(days=30)

    yt_auth_verify = registry.compute_youtube_metrics_expiry(
        authorization_class="AUTHORIZED_CHANNEL",
        fetched_at=now,
    )
    assert yt_auth_verify == now + timedelta(days=30)


def test_workflow_state_machine_all_allowed_and_forbidden_transitions() -> None:
    all_states = list(ContentLifecycleStatus)
    assert len(all_states) == 19

    # Exhaustively verify every (src, dst) pair across the 19x19 transition matrix
    from media_os_api.workflow import HOLD_STATES, STATIC_ALLOWED_TRANSITIONS

    for src in all_states:
        expected_allowed = set(STATIC_ALLOWED_TRANSITIONS[src])
        for dst in all_states:
            actual = is_transition_allowed(src, dst)
            assert actual == (dst in expected_allowed), (
                f"Transition mismatch without previous_status: {src} -> {dst}"
            )

    # Exhaustively verify every primary pipeline progression step
    for idx in range(len(PRIMARY_PIPELINE_ORDER) - 1):
        src = PRIMARY_PIPELINE_ORDER[idx]
        dst = PRIMARY_PIPELINE_ORDER[idx + 1]
        assert is_transition_allowed(src, dst), f"Expected {src} -> {dst} to be allowed"

    # Rework edge allowed
    assert is_transition_allowed(ContentLifecycleStatus.RENDERED, ContentLifecycleStatus.DRAFTED)

    # Exhaustively verify hold-state resume rules for every non-terminal previous_status
    terminal_states = {
        ContentLifecycleStatus.ARCHIVED,
        ContentLifecycleStatus.REJECTED,
        ContentLifecycleStatus.FAILED_FINAL,
    }
    for hold_state in HOLD_STATES:
        for prev in all_states:
            targets = allowed_targets_for(hold_state, previous_status=prev)
            for dst in all_states:
                should_allow = dst in STATIC_ALLOWED_TRANSITIONS[hold_state] or (
                    dst == prev and prev not in HOLD_STATES and prev not in terminal_states
                )
                assert is_transition_allowed(hold_state, dst, previous_status=prev) == should_allow
                assert (dst in targets) == should_allow

    # Explicit sanity assertions on critical human approval & terminal gates
    assert not is_transition_allowed(
        ContentLifecycleStatus.DISCOVERED, ContentLifecycleStatus.PUBLISHED
    )
    assert not is_transition_allowed(
        ContentLifecycleStatus.DRAFTED, ContentLifecycleStatus.USER_APPROVED
    )
    assert not is_transition_allowed(
        ContentLifecycleStatus.QA_PASSED, ContentLifecycleStatus.SCHEDULED
    )
    assert not is_transition_allowed(
        ContentLifecycleStatus.POSTIZ_DRAFT, ContentLifecycleStatus.PUBLISHED
    )
    assert not is_transition_allowed(
        ContentLifecycleStatus.USER_APPROVED, ContentLifecycleStatus.PUBLISHED
    )


def test_task_payload_secret_guard_error_classification_and_backoff() -> None:
    with pytest.raises(JobPayloadSecurityError):
        validate_safe_task_payload({"brand": "tech", "nested": {"api_key": "secret-val"}})
    with pytest.raises(JobPayloadSecurityError):
        validate_safe_task_payload({"password": "123"})

    safe = validate_safe_task_payload({"echo": "ok", "dry_run": True})
    assert safe == {"echo": "ok", "dry_run": True}

    assert classify_exception(RetryableTaskError("net blip")).retryable is True
    assert classify_exception(TimeoutError("timed out")).retryable is True
    assert classify_exception(PermanentTaskError("bad data")).retryable is False
    assert classify_exception(PolicyRefusalError("reddit blocked")).retryable is False
    assert classify_exception(AuthenticationTaskError("bad key")).retryable is False
    assert classify_exception(ValueError("schema mismatch")).retryable is False

    d1 = compute_backoff_seconds(1, base_seconds=2.0, max_seconds=30.0)
    d2 = compute_backoff_seconds(3, base_seconds=2.0, max_seconds=30.0)
    d9 = compute_backoff_seconds(9, base_seconds=2.0, max_seconds=30.0)
    assert 2.0 <= d1 <= 2.4
    assert 8.0 <= d2 <= 9.6
    assert d9 <= 30.0


def test_scheduler_timezone_dst_and_missed_run_anti_burst() -> None:
    # Asia/Kolkata 09:00 IST == 03:30 UTC
    ist = ZoneInfo("Asia/Kolkata")
    prev_ist = datetime(2026, 11, 10, 9, 0, tzinfo=ist).astimezone(UTC)
    now_ist = datetime(2026, 11, 10, 9, 5, tzinfo=ist).astimezone(UTC)
    next_ist = compute_next_run_at(
        {"kind": "daily_time", "hour": 9, "minute": 0},
        "Asia/Kolkata",
        previous_run=prev_ist,
        now=now_ist,
    )
    assert next_ist.astimezone(ist) == datetime(2026, 11, 11, 9, 0, tzinfo=ist)

    # Missed interval while laptop asleep jumps past `now` without burst-firing backlog
    prev_int = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
    now_int = datetime(2026, 11, 10, 1, 25, tzinfo=UTC)
    next_int = compute_next_run_at(
        {"kind": "interval", "seconds": 600},
        "Asia/Kolkata",
        previous_run=prev_int,
        now=now_int,
    )
    assert next_int == datetime(2026, 11, 10, 1, 30, tzinfo=UTC)

    # America/New_York DST spring-forward (2026-03-08 02:00 jumps to 03:00 EDT)
    ny = ZoneInfo("America/New_York")
    spring_prev = datetime(2026, 3, 7, 2, 30, tzinfo=ny).astimezone(UTC)
    spring_now = spring_prev + timedelta(minutes=1)
    spring_next = compute_next_run_at(
        {"kind": "daily_time", "hour": 2, "minute": 30},
        "America/New_York",
        previous_run=spring_prev,
        now=spring_now,
    )
    assert spring_next > spring_now

    # America/New_York DST fall-back (2026-11-01 01:30 occurs twice; fold=0 picks first)
    fall_prev = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0).astimezone(UTC)
    fall_now = fall_prev + timedelta(minutes=10)
    fall_next = compute_next_run_at(
        {"kind": "daily_time", "hour": 1, "minute": 30},
        "America/New_York",
        previous_run=fall_prev,
        now=fall_now,
    )
    assert fall_next.astimezone(ny).date() == datetime(2026, 11, 2, 1, 30, tzinfo=ny).date()


def test_celery_app_uses_json_only_and_explicit_queues() -> None:
    app = build_celery_app(
        load_settings(
            {
                "OWNER_USERNAME": "owner",
                "OWNER_PASSWORD_HASH": _sample_hash(),
                "SESSION_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef",
                "REDIS_URL": "redis://:testpass@localhost:6379/0",
            }
        )
    )
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.result_serializer == "json"
    assert app.conf.worker_concurrency == 1
    assert app.conf.task_acks_late is True
    queue_names = {q.name for q in CELERY_QUEUES}
    assert {"control", "maintenance", "default", "research", "creative"}.issubset(queue_names)


def test_redis_rate_limiter_outage_policies() -> None:
    class _FailingRedis:
        def pipeline(self) -> object:
            raise RedisConnectionError("Redis unavailable")

        def delete(self, *_args: object) -> None:
            raise RedisConnectionError("Redis unavailable")

    # Fallback mode preserves brute-force protection during Redis outage
    fallback_limiter = RedisLoginRateLimiter(
        _FailingRedis(),  # type: ignore[arg-type]
        max_attempts=2,
        window_seconds=60,
        fail_closed_on_outage=False,
    )
    assert fallback_limiter.check("127.0.0.1", "owner").allowed is True
    fallback_limiter.record_failure("127.0.0.1", "owner")
    fallback_limiter.record_failure("127.0.0.1", "owner")
    blocked = fallback_limiter.check("127.0.0.1", "owner")
    assert blocked.allowed is False
    assert blocked.backend == "memory"
    assert blocked.retry_after_seconds >= 1

    # Fail-closed mode raises RateLimiterUnavailableError on Redis outage
    strict_limiter = RedisLoginRateLimiter(
        _FailingRedis(),  # type: ignore[arg-type]
        max_attempts=2,
        window_seconds=60,
        fail_closed_on_outage=True,
    )
    with pytest.raises(RateLimiterUnavailableError):
        strict_limiter.check("127.0.0.1", "owner")

    # Sanity check direct memory limiter stays bounded
    mem = LoginRateLimiter(max_attempts=2, window_seconds=60, max_keys=2)
    mem.record_failure("1.1.1.1", "u1")
    mem.record_failure("2.2.2.2", "u2")
    assert mem.check("1.1.1.1", "u1").allowed is True


def test_rotate_password_cli_guards_env_override_and_external_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import base64
    import contextlib
    import getpass
    import os
    import sys
    from types import SimpleNamespace

    from media_os_api.auth import rotate_password

    repo_root = Path(__file__).resolve().parents[2]

    # 1. Refuses blank username, .env.example, and inside-repo --env-file paths
    monkeypatch.setattr(sys, "argv", ["rotate_password", "--username", "   "])
    with pytest.raises(SystemExit, match="must not be blank"):
        rotate_password.main()

    monkeypatch.setattr(
        sys,
        "argv",
        ["rotate_password", "--username", "owner", "--env-file", str(repo_root / ".env.example")],
    )
    with pytest.raises(SystemExit, match=r"\.env\.example"):
        rotate_password.main()

    monkeypatch.setattr(
        sys,
        "argv",
        ["rotate_password", "--username", "owner", "--env-file", str(repo_root / ".env")],
    )
    with pytest.raises(SystemExit, match="inside the Git repository"):
        rotate_password.main()

    # 2. Password confirmation mismatch and < 8 chars exit cleanly without touching DB
    ext_env = tmp_path / "runtime.env"
    initial_hash = _sample_hash()
    ext_env.write_text(
        "\n".join(
            [
                "OWNER_USERNAME=owner",
                f"OWNER_PASSWORD_HASH={initial_hash}",
                "SESSION_SECRET=0123456789abcdef0123456789abcdef0123456789abcdef",
                "POSTGRES_DB=media_os_test",
                "DATABASE_URL=postgresql+psycopg://u:p@db:5432/media_os_test",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_DB", "media_os")
    monkeypatch.setenv("OWNER_PASSWORD_HASH", "")
    monkeypatch.setenv("OWNER_PASSWORD_HASH_B64", "")

    prompts_mismatch = iter(["ValidLongPass!123", "DifferentLongPass!123"])
    monkeypatch.setattr(getpass, "getpass", lambda _prompt="": next(prompts_mismatch))
    monkeypatch.setattr(
        sys,
        "argv",
        ["rotate_password", "--username", "owner", "--env-file", str(ext_env)],
    )
    with pytest.raises(SystemExit, match="did not match"):
        rotate_password.main()
    # Explicit --env-file must override pre-existing empty/default env vars
    assert os.environ["POSTGRES_DB"] == "media_os_test"
    assert os.environ["DATABASE_URL"] == "postgresql+psycopg://u:p@db:5432/media_os_test"

    prompts_short = iter(["short7", "short7"])
    monkeypatch.setattr(getpass, "getpass", lambda _prompt="": next(prompts_short))
    with pytest.raises(SystemExit, match="at least 8 characters"):
        rotate_password.main()

    # 3. Successful rotation updates DB hook and rewrites external .env as OWNER_PASSWORD_HASH_B64
    new_plain = "Rotated-Strong-Secret!2026"
    new_argon_hash = hash_password(new_plain)
    prompts_ok = iter([new_plain, new_plain])
    monkeypatch.setattr(getpass, "getpass", lambda _prompt="": next(prompts_ok))

    disposed = {"done": False}

    class _DummyEngine:
        def dispose(self) -> None:
            disposed["done"] = True

    @contextlib.contextmanager
    def _fake_scope(_factory: object):  # type: ignore[no-untyped-def]
        yield object()

    captured_args: dict[str, object] = {}

    def _fake_rotate(
        _db: object,
        *,
        username: str,
        new_password: str,
        actor_id: str,
    ) -> SimpleNamespace:
        captured_args["username"] = username
        captured_args["new_password"] = new_password
        captured_args["actor_id"] = actor_id
        return SimpleNamespace(password_hash=new_argon_hash, token_version=2)

    monkeypatch.setattr(
        rotate_password, "create_db_engine_from_settings", lambda _s: _DummyEngine()
    )
    monkeypatch.setattr(rotate_password, "create_session_factory", lambda _e: object())
    monkeypatch.setattr(rotate_password, "session_scope", _fake_scope)
    monkeypatch.setattr(rotate_password, "rotate_owner_password", _fake_rotate)

    rc = rotate_password.main()
    assert rc == 0
    assert disposed["done"] is True
    assert captured_args == {
        "username": "owner",
        "new_password": new_plain,
        "actor_id": "cli:rotate_password",
    }

    updated_text = ext_env.read_text(encoding="utf-8")
    assert "OWNER_PASSWORD_HASH=" not in updated_text
    expected_b64 = base64.b64encode(new_argon_hash.encode("utf-8")).decode("ascii")
    assert f"OWNER_PASSWORD_HASH_B64={expected_b64}" in updated_text

    out = capsys.readouterr().out
    assert "token_version=2" in out
    assert new_plain not in out
    assert new_argon_hash not in out
    assert expected_b64 not in out
