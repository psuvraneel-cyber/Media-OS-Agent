"""Unit: Telegram client, plain-language Postiz errors, alert texts, and the send handler."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from media_os_api.db.models import JobRun
from media_os_api.jobs.errors import PermanentTaskError, RetryableTaskError
from media_os_api.notify import messages as msg
from media_os_api.notify.service import handle_send_telegram
from media_os_api.notify.telegram import (
    MAX_TEXT,
    TelegramClient,
    TelegramError,
    telegram_from_env,
)

TOKEN = "123456789:AAH-test_token_value_abcdefghijklmnopqrstu"
CHAT = "987654321"


def _client(handler: Any) -> TelegramClient:
    return TelegramClient(token=TOKEN, chat_id=CHAT, transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ client


def test_send_message_posts_html_to_the_fixed_host() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    with _client(handler) as client:
        sent = client.send_message("<b>hi</b>", silent=True)
    assert sent.message_id == 42
    assert seen["url"].startswith("https://api.telegram.org/bot")
    assert seen["url"].endswith("/sendMessage")
    assert seen["body"] == {
        "chat_id": CHAT,
        "text": "<b>hi</b>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": True,
    }


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "TELEGRAM_RATE_LIMITED", True),
        (502, "TELEGRAM_SERVER", True),
        (401, "TELEGRAM_UNAUTHORIZED", False),
        (403, "TELEGRAM_FORBIDDEN", False),
        (400, "TELEGRAM_BAD_REQUEST", False),
    ],
)
def test_errors_are_classified(status: int, code: str, retryable: bool) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"ok": False, "description": "nope"})

    with _client(handler) as client, pytest.raises(TelegramError) as info:
        client.send_message("x")
    assert info.value.code == code
    assert info.value.retryable is retryable
    assert TOKEN not in str(info.value)


def test_network_error_never_leaks_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom connecting to {request.url}", request=request)

    with _client(handler) as client, pytest.raises(TelegramError) as info:
        client.send_message("x")
    assert info.value.code == "TELEGRAM_NETWORK" and info.value.retryable
    assert TOKEN not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_malformed_settings_are_refused_without_echoing_them() -> None:
    with pytest.raises(TelegramError) as info:
        TelegramClient(token="not-a-token-secret-value", chat_id=CHAT)
    assert info.value.code == "TELEGRAM_BAD_TOKEN"
    assert "not-a-token-secret-value" not in str(info.value)
    with pytest.raises(TelegramError) as info:
        TelegramClient(token=TOKEN, chat_id="chat?")
    assert info.value.code == "TELEGRAM_BAD_CHAT"


def test_long_text_is_truncated() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["text"] = json.loads(request.content)["text"]
        return httpx.Response(200, json={"ok": True, "result": {}})

    with _client(handler) as client:
        client.send_message("a" * 10_000)
    assert len(seen["text"]) == MAX_TEXT and seen["text"].endswith("…")


def test_from_env_is_none_until_both_values_are_set() -> None:
    assert telegram_from_env({}) is None
    assert telegram_from_env({"TELEGRAM_BOT_TOKEN": TOKEN}) is None
    assert telegram_from_env({"TELEGRAM_CHAT_ID": CHAT}) is None
    client = telegram_from_env({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT})
    assert client is not None
    client.close()


# ------------------------------------------------------------------ Postiz errors

# Shape of the real error Postiz stored for the 15 Nov 2026 failure (trimmed).
META_BLOCKED = json.dumps(
    {
        "cause": {"failure": {"message": "Unknown Error"}},
        "type": "bad_body",
        "details": [
            {
                "identifier": "upload images slides",
                "json": json.dumps(
                    {
                        "error": {
                            "message": (
                                "Cannot call API for app 1862070358151146 on behalf of user "
                                "1664262358700884"
                            ),
                            "type": "OAuthException",
                            "code": 200,
                        }
                    }
                ),
            }
        ],
    }
)


def _meta(message: str, code: int) -> str:
    return json.dumps(
        {"details": [{"json": json.dumps({"error": {"message": message, "code": code}})}]}
    )


def test_meta_app_block_is_explained() -> None:
    text = msg.explain_postiz_error(META_BLOCKED)
    assert "Meta blocked the app" in text and "reconnect the channel in Postiz" in text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            _meta("Error validating access token: Session has expired", 190),
            "login for this channel expired",
        ),
        (_meta("Application request limit reached", 4), "rate limit"),
        (
            _meta("Media download has failed. The media URI doesn't meet our requirements", 9004),
            "couldn't download the images",
        ),
        (
            _meta("(#10) Application does not have permission for this action", 10),
            "refused a permission",
        ),
        ("", "without giving a reason"),
        ("plain failure text", "Postiz reported: plain failure text"),
    ],
)
def test_other_errors_are_explained(raw: str, expected: str) -> None:
    assert expected in msg.explain_postiz_error(raw)


# ------------------------------------------------------------------ alert texts


def _ref(title: str = "বিয়ের কার্ডে *দেরি?*") -> msg.PostRef:
    return msg.PostRef(
        title=title,
        brand="Nimontron",
        platform="INSTAGRAM",
        scheduled_at=datetime(2026, 11, 15, 13, 30, tzinfo=UTC),
        url="https://www.instagram.com/p/abc/",
    )


def test_failed_alert_has_reason_time_and_no_markers() -> None:
    text = msg.post_failed(_ref(), META_BLOCKED)
    assert "Post failed on Instagram" in text
    assert "15 Nov, 7:00 PM IST" in text
    assert "Meta blocked the app" in text
    assert "*" not in text  # *emphasis* markers stripped from the title


def test_untrusted_text_is_escaped() -> None:
    text = msg.post_published(_ref(title="<script>x</script> & co"), late_by_minutes=12)
    assert "<script>" not in text and "&lt;script&gt;" in text and "&amp; co" in text
    assert "(12 min late)" in text


def test_digest_lists_counts_and_review_link() -> None:
    item = msg.DigestItem(
        title="Hook", platform="FACEBOOK", at=datetime(2026, 11, 16, 13, 30, tzinfo=UTC)
    )
    text = msg.daily_digest(
        day=datetime(2026, 11, 16, 15, 30, tzinfo=UTC),
        published=[item],
        failed=[],
        upcoming=[item, item],
        awaiting_review=3,
        dashboard_url="http://127.0.0.1:3100",
    )
    assert "Posted today:</b> 1" in text and "Coming up (next 24 h):</b> 2" in text
    assert "3</b> carousel(s) waiting" in text and "/approvals" in text


# ------------------------------------------------------------------ send handler


class _FakeTelegram:
    def __init__(self, error: TelegramError | None = None) -> None:
        self.error = error
        self.sent: list[tuple[str, bool]] = []

    def send_message(self, text: str, *, silent: bool = False, **_: Any) -> Any:
        if self.error:
            raise self.error
        self.sent.append((text, silent))
        return type("Sent", (), {"message_id": 7})()

    def close(self) -> None:
        return None


def _job(**payload: Any) -> JobRun:
    return JobRun(task_name="media_os.notify.telegram", payload=payload)


def test_send_handler_skips_when_not_configured() -> None:
    result = handle_send_telegram(None, _job(text="hi"), telegram=lambda: None)  # type: ignore[arg-type]
    assert result == {"sent": False, "reason": "TELEGRAM_NOT_CONFIGURED"}


def test_send_handler_sends_and_maps_errors() -> None:
    fake = _FakeTelegram()
    result = handle_send_telegram(None, _job(text="hi", silent=True), telegram=lambda: fake)  # type: ignore[arg-type,return-value]
    assert result["sent"] is True and fake.sent == [("hi", True)]

    retry = _FakeTelegram(TelegramError("down", code="TELEGRAM_NETWORK", retryable=True))
    with pytest.raises(RetryableTaskError):
        handle_send_telegram(None, _job(text="hi"), telegram=lambda: retry)  # type: ignore[arg-type,return-value]

    perm = _FakeTelegram(TelegramError("bad", code="TELEGRAM_UNAUTHORIZED", retryable=False))
    with pytest.raises(PermanentTaskError):
        handle_send_telegram(None, _job(text="hi"), telegram=lambda: perm)  # type: ignore[arg-type,return-value]

    with pytest.raises(PermanentTaskError):
        handle_send_telegram(None, _job(text="  "), telegram=lambda: fake)  # type: ignore[arg-type,return-value]


# ------------------------------------------------------------------ nightly report


def test_metrics_block_formats_totals_growth_and_notes() -> None:
    from media_os_api.analytics.service import MetricTotal, PeriodTotals, PlatformTotals, TopPost

    now = datetime(2026, 11, 16, 16, 30, tzinfo=UTC)
    totals = PeriodTotals(
        start=now,
        end=now,
        platforms=(
            PlatformTotals(
                "INSTAGRAM",
                3,
                (MetricTotal("views", 1234, 1000, False), MetricTotal("likes", 56, None, False)),
            ),
        ),
        overall=(MetricTotal("views", 1234, 1000, False),),
        top_post=TopPost("Hook *one*", "INSTAGRAM", 40, 900, "https://www.instagram.com/p/x/"),
        data_as_of=now,
        platforms_seen=frozenset({"INSTAGRAM"}),
    )
    text = "\n".join(msg.metrics_block(totals))
    assert "All platforms:</b> Views 1,234 (▲23%)" in text
    assert "<b>Instagram</b> (3 posts): Views 1,234 (▲23%) · Likes 56" in text
    assert "Top post:</b> Hook one (Instagram, 40 interactions)" in text
    assert "YouTube isn't connected" in text and "numbers as of 16 Nov, 10:00 PM IST" in text
    assert "Facebook" not in text


def test_metrics_block_without_data() -> None:
    assert "No published posts with numbers yet." in "\n".join(msg.metrics_block(None))


def test_nightly_refresh_rechecks_recent_posts_only() -> None:
    from datetime import timedelta

    from media_os_api.analytics.sync import is_due
    from media_os_api.db.models import PublishSchedule

    now = datetime(2026, 11, 16, 16, 10, tzinfo=UTC)
    recent = PublishSchedule(
        scheduled_at=now - timedelta(days=3), metrics_checked_at=now - timedelta(hours=2)
    )
    just_read = PublishSchedule(
        scheduled_at=now - timedelta(days=3), metrics_checked_at=now - timedelta(minutes=20)
    )
    old = PublishSchedule(
        scheduled_at=now - timedelta(days=60), metrics_checked_at=now - timedelta(hours=2)
    )
    assert not is_due(recent, now) and is_due(recent, now, force_recent=True)
    assert not is_due(just_read, now, force_recent=True)  # read in the last hour
    assert not is_due(old, now, force_recent=True)  # older than 30 days keeps its tier


# ------------------------------------------------------------------ auto-retry + bot commands


def test_transient_errors_are_retried_and_permission_errors_are_not() -> None:
    assert msg.is_transient_postiz_error(_meta("Application request limit reached", 4))
    assert msg.is_transient_postiz_error(_meta("Media download has failed", 9004))
    assert msg.is_transient_postiz_error(_meta("An unexpected error has occurred. Please retry", 2))
    assert msg.is_transient_postiz_error("Service temporarily unavailable")
    assert not msg.is_transient_postiz_error(META_BLOCKED)  # Meta blocked the app
    assert not msg.is_transient_postiz_error(_meta("Error validating access token", 190))
    assert not msg.is_transient_postiz_error(
        _meta("(#10) Application does not have permission", 10)
    )
    assert not msg.is_transient_postiz_error("")  # no reason given: tell the owner


def test_failed_alert_mentions_the_automatic_retry() -> None:
    retry = datetime(2026, 11, 16, 14, 1, tzinfo=UTC)
    text = msg.post_failed(_ref(), _meta("Application request limit reached", 4), retry_at=retry)
    assert "retry automatically at 16 Nov, 7:31 PM IST" in text
    assert "reschedule it from Approvals" not in text


def test_bot_replies_route_to_the_right_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from media_os_api.notify import commands

    calls: list[str] = []

    def fake_status(_s: Any, _n: Any) -> str:
        calls.append("status")
        return "S"

    def fake_queue(_s: Any, _n: Any) -> str:
        calls.append("queue")
        return "Q"

    monkeypatch.setattr(commands, "status_text", fake_status)
    monkeypatch.setattr(commands, "queue_text", fake_queue)
    now = datetime(2026, 11, 16, tzinfo=UTC)
    assert commands.reply_for(None, "/status", now) == "S"  # type: ignore[arg-type]
    assert commands.reply_for(None, "/queue@Media_bot extra", now) == "Q"  # type: ignore[arg-type]
    assert commands.reply_for(None, "/help", now) == commands.HELP  # type: ignore[arg-type]
    assert commands.reply_for(None, "/whatever", now) == commands.HELP  # type: ignore[arg-type]
    assert calls == ["status", "queue"]


def test_get_updates_reads_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/getUpdates")
        assert json.loads(request.content)["offset"] == 5
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 5, "message": {}}]})

    with _client(handler) as client:
        assert client.get_updates(offset=5) == [{"update_id": 5, "message": {}}]
