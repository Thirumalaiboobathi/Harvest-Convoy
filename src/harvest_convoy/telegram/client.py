"""Thin wrapper over the Telegram Bot API. Raw HTTPS + JSON, no framework --
see docs/adr/ADR-004-telegram.md Decision 1 for why no new dependency was
added for this.

TELEGRAM_BOT_TOKEN was absent from .env at the time this was written --
nothing here has been run against a real bot. See ADR-004's header note.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from harvest_convoy.observability.otel import get_tracer

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

API_BASE = "https://api.telegram.org"
SEND_RETRY_ATTEMPTS = 3
SEND_RETRY_BACKOFF_SECONDS = (1.0, 2.0)  # between attempts 1->2 and 2->3


@dataclass(frozen=True)
class SendResult:
    success: bool
    error: str | None = None
    response: dict[str, Any] | None = None


class TelegramClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN not set -- client constructed but any "
                "call will fail until a real token is provided."
            )

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(self._url(method), json=payload, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram API error on {method}: {data}")
        return data

    def _call_with_retry(self, method: str, payload: dict[str, Any]) -> SendResult:
        with tracer.start_as_current_span(
            f"telegram.{method}", attributes={"chat_id": str(payload.get("chat_id", ""))}
        ) as span:
            last_error: str | None = None
            for attempt in range(SEND_RETRY_ATTEMPTS):
                try:
                    data = self._call(method, payload)
                    return SendResult(success=True, response=data.get("result"))
                except Exception as exc:  # noqa: BLE001 -- degrade, never throw
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "telegram %s attempt %d/%d failed: %s",
                        method, attempt + 1, SEND_RETRY_ATTEMPTS, last_error,
                    )
                    if attempt < len(SEND_RETRY_BACKOFF_SECONDS):
                        time.sleep(SEND_RETRY_BACKOFF_SECONDS[attempt])

            span.set_attribute("degraded", True)
            logger.error(
                "telegram %s failed after %d attempts: %s",
                method, SEND_RETRY_ATTEMPTS, last_error,
            )
            return SendResult(success=False, error=last_error)

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> SendResult:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call_with_retry("sendMessage", payload)

    def answer_callback_query(
        self, callback_query_id: str, text: str | None = None, show_alert: bool = False
    ) -> SendResult:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
            payload["show_alert"] = show_alert
        return self._call_with_retry("answerCallbackQuery", payload)

    def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> SendResult:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup or {"inline_keyboard": []},
        }
        return self._call_with_retry("editMessageReplyMarkup", payload)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> SendResult:
        """Added for ADR-013's route-proposal edit-in-place UI: swap/drop/
        Done taps rewrite the same message's text and keyboard together
        (Telegram's editMessageText accepts both in one call) rather than
        sending a new message per edit."""
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call_with_retry("editMessageText", payload)
