"""Minimal, hardened Telegram Bot API client for owner alerts.

Telegram puts the bot token in the URL path (`/bot<token>/sendMessage`), so this client is
deliberately separate from the research HTTP client (which logs URLs). Guarantees:

* exactly one fixed host (`api.telegram.org`), https only, no redirects, no proxy env vars;
* the token is held in memory only and never appears in logs, exceptions or return values
  (httpx errors carry the request URL, so they are caught and re-raised without it);
* failures are classified like the job system: network, 429 and 5xx are retryable; a bad token,
  a blocked bot or a wrong chat id are permanent.

Configuration comes from the worker environment, like the other publishing credentials:
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. When either is missing, `telegram_from_env` returns
None and alerts are skipped (the system keeps working without them).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

API_HOST = "api.telegram.org"
MAX_TEXT = 4000  # Telegram's limit is 4096 characters; keep a margin for the truncation marker
_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")
_CHAT_RE = re.compile(r"^-?\d{3,20}$|^@[A-Za-z0-9_]{5,32}$")


class TelegramError(Exception):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class SentMessage:
    message_id: int | None


class TelegramClient:
    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        token = token.strip()
        chat_id = chat_id.strip()
        if not _TOKEN_RE.match(token):
            # Never echo the value: only say it is malformed.
            raise TelegramError(
                "TELEGRAM_BOT_TOKEN is malformed.", code="TELEGRAM_BAD_TOKEN", retryable=False
            )
        if not _CHAT_RE.match(chat_id):
            raise TelegramError(
                "TELEGRAM_CHAT_ID is malformed.", code="TELEGRAM_BAD_CHAT", retryable=False
            )
        self._token = token
        self.chat_id = chat_id
        # The bot's own user id: the part of the token before the colon (public, not secret).
        self.bot_id = token.split(":", 1)[0]
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TelegramClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def send_message(
        self,
        text: str,
        *,
        buttons: list[list[Mapping[str, str]]] | None = None,
        silent: bool = False,
    ) -> SentMessage:
        """Send an HTML-formatted message (callers must escape untrusted text)."""
        body: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": truncate(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        }
        if buttons:
            body["reply_markup"] = {"inline_keyboard": [list(row) for row in buttons]}
        data = self._call("sendMessage", body)
        result = data.get("result") if isinstance(data, dict) else None
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return SentMessage(message_id=message_id if isinstance(message_id, int) else None)

    def send_photos(self, photos: list[bytes], *, silent: bool = True) -> None:
        """Send 2-10 images as one album (sendMediaGroup), or a single photo (sendPhoto)."""
        if not photos:
            return
        photos = photos[:10]
        if len(photos) == 1:
            self._call_files(
                "sendPhoto",
                {"chat_id": self.chat_id, "disable_notification": json.dumps(silent)},
                {"photo": ("slide-1.png", photos[0], "image/png")},
            )
            return
        media = [{"type": "photo", "media": f"attach://slide{i}"} for i in range(len(photos))]
        self._call_files(
            "sendMediaGroup",
            {
                "chat_id": self.chat_id,
                "media": json.dumps(media),
                "disable_notification": json.dumps(silent),
            },
            {
                f"slide{i}": (f"slide-{i + 1}.png", data, "image/png")
                for i, data in enumerate(photos)
            },
        )

    def ask(self, text: str, *, placeholder: str = "") -> SentMessage:
        """A message the owner answers by replying (Telegram opens the reply box for them)."""
        markup: dict[str, Any] = {"force_reply": True, "selective": True}
        if placeholder:
            markup["input_field_placeholder"] = placeholder[:64]
        data = self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": truncate(text),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": markup,
            },
        )
        result = data.get("result") if isinstance(data, dict) else None
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return SentMessage(message_id=message_id if isinstance(message_id, int) else None)

    def answer_button(self, callback_id: str, text: str = "") -> None:
        """Stop the button's loading spinner, with an optional toast."""
        try:
            self._call(
                "answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]}
            )
        except TelegramError as exc:
            if exc.code != "TELEGRAM_BAD_REQUEST":  # an old press can't be answered: fine
                raise

    def replace_buttons(
        self, message_id: int, buttons: list[list[Mapping[str, str]]] | None
    ) -> None:
        """Swap a card's buttons (None removes them), e.g. once it's been decided."""
        try:
            self._call(
                "editMessageReplyMarkup",
                {
                    "chat_id": self.chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": [list(r) for r in (buttons or [])]},
                },
            )
        except TelegramError as exc:
            if exc.code != "TELEGRAM_BAD_REQUEST":  # "message is not modified": fine
                raise

    def get_updates(self, *, offset: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """New messages and button presses sent to the bot (short poll: returns immediately)."""
        body: dict[str, Any] = {
            "timeout": 0,
            "limit": limit,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            body["offset"] = offset
        data = self._call("getUpdates", body)
        result = data.get("result") if isinstance(data, dict) else None
        return [u for u in result or [] if isinstance(u, dict)] if isinstance(result, list) else []

    def _call(self, method: str, body: Mapping[str, Any]) -> Any:
        return self._post(method, json=body)

    def _call_files(
        self,
        method: str,
        fields: Mapping[str, str],
        files: Mapping[str, tuple[str, bytes, str]],
    ) -> Any:
        """Multipart upload (photos). Uploads get a longer timeout than plain messages."""
        return self._post(method, data=dict(fields), files=dict(files), timeout=60.0)

    def _post(self, method: str, *, timeout: float | None = None, **kwargs: Any) -> Any:
        url = f"https://{API_HOST}/bot{self._token}/{method}"
        try:
            if timeout is not None:
                kwargs["timeout"] = httpx.Timeout(timeout, connect=5.0)
            response = self._client.post(url, **kwargs)
        except httpx.TimeoutException:
            raise TelegramError(
                "Telegram request timed out.", code="TELEGRAM_TIMEOUT", retryable=True
            ) from None
        except httpx.HTTPError as exc:
            # Re-raise without the original exception: its text includes the URL (and token).
            raise TelegramError(
                f"Telegram unreachable ({type(exc).__name__}).",
                code="TELEGRAM_NETWORK",
                retryable=True,
            ) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        description = ""
        if isinstance(data, dict):
            description = str(data.get("description") or "")[:200]
        status = response.status_code
        if 200 <= status < 300 and isinstance(data, dict) and data.get("ok"):
            return data
        if status == 429:
            raise TelegramError(
                "Telegram rate limit reached.", code="TELEGRAM_RATE_LIMITED", retryable=True
            )
        if status >= 500:
            raise TelegramError(
                f"Telegram server error ({status}).", code="TELEGRAM_SERVER", retryable=True
            )
        if status == 401:
            raise TelegramError(
                "Telegram rejected the bot token.", code="TELEGRAM_UNAUTHORIZED", retryable=False
            )
        if status == 403:
            raise TelegramError(
                "The bot can't message this chat (send /start to the bot first).",
                code="TELEGRAM_FORBIDDEN",
                retryable=False,
            )
        raise TelegramError(
            f"Telegram refused the message ({status}): {description}",
            code="TELEGRAM_BAD_REQUEST",
            retryable=False,
        )


def truncate(text: str) -> str:
    if len(text) <= MAX_TEXT:
        return text
    return text[: MAX_TEXT - 1] + "…"


def telegram_from_env(
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> TelegramClient | None:
    """A client from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, or None when not configured."""
    source = os.environ if env is None else env
    token = (source.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (source.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return None
    return TelegramClient(token=token, chat_id=chat_id, transport=transport)
