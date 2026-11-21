"""Unit: reviewing carousels from Telegram (client calls, card text, buttons)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from media_os_api.db.models import ContentPackage
from media_os_api.notify.review import card_text, review_buttons
from media_os_api.notify.telegram import TelegramClient, TelegramError

pytestmark = pytest.mark.unit

TOKEN = "123456789:AAH-test_token_value_abcdefghijklmnopqrstu"
CHAT = "987654321"


def _client(handler: Any) -> TelegramClient:
    return TelegramClient(token=TOKEN, chat_id=CHAT, transport=httpx.MockTransport(handler))


def test_slides_go_as_one_album_upload() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        seen["type"] = request.headers["content-type"]
        return httpx.Response(200, json={"ok": True, "result": []})

    with _client(handler) as client:
        client.send_photos([b"png-one", b"png-two", b"png-three"])
    assert seen["path"].endswith("/sendMediaGroup")
    assert seen["type"].startswith("multipart/form-data")
    body = seen["body"].decode("latin-1")
    assert "attach://slide0" in body and "attach://slide2" in body
    assert "png-two" in body and TOKEN not in body


def test_one_slide_uses_send_photo_and_none_sends_nothing() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {}})

    with _client(handler) as client:
        client.send_photos([b"only"])
        client.send_photos([])
    assert len(paths) == 1 and paths[0].endswith("/sendPhoto")


def test_questions_open_the_reply_box_and_updates_include_button_presses() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    with _client(handler) as client:
        assert client.ask("Why?", placeholder="Reason").message_id == 5
        client.get_updates(offset=3)
    assert bodies[0]["reply_markup"] == {
        "force_reply": True,
        "selective": True,
        "input_field_placeholder": "Reason",
    }
    assert bodies[1]["allowed_updates"] == ["message", "callback_query"]


def test_old_button_presses_and_unchanged_cards_are_not_errors() -> None:
    def refused(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "query is too old"})

    with _client(refused) as client:
        client.answer_button("q1", "hi")  # no exception
        client.replace_buttons(7, None)  # no exception

    def down(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"ok": False})

    with _client(down) as client, pytest.raises(TelegramError) as err:
        client.answer_button("q1")
    assert err.value.retryable


def test_buttons_fit_telegram_limits() -> None:
    pid = uuid.uuid4()
    rows = review_buttons(pid)
    data = [b["callback_data"] for row in rows for b in row if "callback_data" in b]
    assert data == [f"ap:{pid}", f"rj:{pid}", f"ed:{pid}"]
    assert all(len(d.encode()) <= 64 for d in data)  # Telegram's callback_data limit


def test_card_shows_the_post_and_escapes_its_text() -> None:
    pkg = ContentPackage(
        title="Wedding <season> & you",
        caption="Line one\n\nGet it: https://play.google.com/x",
        hashtags=["#Nimontron"],
        slide_data=[{}, {}, {}],
        post_tags={"pillar": "wedding_season", "language": "BN_EN"},
        qa_report={"checks": [{"name": "shows_a_card", "status": "WARN", "detail": "no card"}]},
        score={"total": 82},
    )
    text = card_text(pkg)
    assert "Wedding &lt;season&gt; &amp; you" in text
    assert "wedding season · Bilingual · 3 slides" in text
    assert "⚠️ no card" in text and "#Nimontron" in text
    assert "heuristic" in text  # the learned score isn't trusted yet
    pkg.score = {"total": 82, "learned": {"active": True, "predicted_lift": 1.8}}
    assert "Expected: 1.8× your usual views" in card_text(pkg)
