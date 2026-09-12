"""Telegram message streaming helpers (throttle + 4096-char splitting)."""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def escape_md(text: str) -> str:
    """Escape dynamic text for Telegram legacy Markdown parse_mode."""
    if not text:
        return ""
    out = str(text)
    for ch in ("\\", "`", "*", "_", "["):
        out = out.replace(ch, f"\\{ch}")
    return out


def escape_html(text: str) -> str:
    """Escape dynamic text for Telegram HTML parse_mode."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def split_telegram_text(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into chunks that fit Telegram's message length limit."""
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks or [""]


class StreamingTelegramSink:
    """
    Buffers agent output and edits a Telegram message on a throttle interval.

    When the buffer exceeds the Telegram limit, prior content is finalized into
    sequential messages and editing continues on a fresh message.
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        *,
        edit_interval: float = 1.5,
        prefix: str = "",
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._edit_interval = edit_interval
        self._prefix = prefix
        self._buffer = ""
        self._message: Message | None = None
        self._dirty = False
        self._closed = False
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task[None] | None = None

    async def start(self, initial_text: str | None = None) -> None:
        """Begin streaming. Pass None to wait until the first real content."""
        if initial_text is not None:
            self._message = await self._bot.send_message(self._chat_id, initial_text)
        self._flusher = asyncio.create_task(self._flush_loop(), name="tg-stream-flush")

    async def append(self, text: str) -> None:
        if not text or self._closed:
            return
        async with self._lock:
            self._buffer += text
            self._dirty = True
            await self._maybe_roll_messages()

    async def set_status(self, status: str) -> None:
        """Replace buffer with a short status (used before streaming starts)."""
        async with self._lock:
            self._buffer = status
            self._dirty = True

    async def finalize(self, footer: str | None = None) -> str:
        """Flush remaining text. Returns the final buffer body (no prefix)."""
        self._closed = True
        if self._flusher and not self._flusher.done():
            self._flusher.cancel()
            try:
                await self._flusher
            except asyncio.CancelledError:
                pass
        async with self._lock:
            if footer:
                self._buffer = (self._buffer + footer).rstrip()
            body = self._buffer.strip()
            if body:
                await self._flush_locked(force=True)
            elif self._message is not None:
                # No useful reply — remove the placeholder if we created one
                try:
                    await self._message.delete()
                except Exception:
                    logger.debug("Could not delete empty stream message", exc_info=True)
            return body

    async def _flush_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self._edit_interval)
                async with self._lock:
                    if self._dirty:
                        await self._flush_locked(force=False)
        except asyncio.CancelledError:
            return

    async def _maybe_roll_messages(self) -> None:
        """If buffer is too large, send completed chunks as new messages."""
        display = self._display_text()
        # Keep headroom so we can still edit mid-stream
        soft_limit = TELEGRAM_MAX_MESSAGE_LENGTH - 80
        while len(display) > soft_limit:
            chunks = split_telegram_text(display, soft_limit)
            # Finalize all but the last chunk into separate messages
            for chunk in chunks[:-1]:
                await self._edit_or_send(chunk)
                # Start a fresh message for the remainder
                self._message = await self._bot.send_message(self._chat_id, "…")
            # Keep only the last chunk in the logical buffer (strip prefix handling)
            remainder = chunks[-1]
            if self._prefix and remainder.startswith(self._prefix):
                self._buffer = remainder[len(self._prefix) :]
            else:
                self._buffer = remainder
            display = self._display_text()
            self._dirty = True

    def _display_text(self) -> str:
        body = self._buffer if self._buffer.strip() else "…"
        return f"{self._prefix}{body}" if self._prefix else body

    async def _flush_locked(self, *, force: bool) -> None:
        if not force and not self._dirty:
            return
        await self._maybe_roll_messages()
        text = self._display_text()
        # Hard clamp for safety
        if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH:
            for chunk in split_telegram_text(text):
                await self._edit_or_send(chunk)
                self._message = await self._bot.send_message(self._chat_id, "…")
            self._buffer = ""
            self._dirty = False
            return
        await self._edit_or_send(text)
        self._dirty = False

    async def _edit_or_send(self, text: str) -> None:
        if not text:
            text = "…"
        if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH:
            text = text[: TELEGRAM_MAX_MESSAGE_LENGTH - 1] + "…"
        try:
            if self._message is None:
                self._message = await self._bot.send_message(self._chat_id, text)
                return
            if self._message.text == text:
                return
            await self._message.edit_text(text)
        except TelegramRetryAfter as exc:
            logger.warning("Telegram rate limited; sleeping %.1fs", exc.retry_after)
            await asyncio.sleep(float(exc.retry_after) + 0.1)
            try:
                await self._message.edit_text(text)  # type: ignore[union-attr]
            except TelegramBadRequest:
                self._message = await self._bot.send_message(self._chat_id, text)
        except TelegramBadRequest as exc:
            # Message is not modified, or content invalid — fall back to send
            if "message is not modified" in str(exc).lower():
                return
            logger.debug("edit_text failed (%s); sending new message", exc)
            self._message = await self._bot.send_message(self._chat_id, text)


async def send_long_message(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
) -> Sequence[Message]:
    """Send a (possibly long) message as one or more Telegram messages."""
    messages: list[Message] = []
    for chunk in split_telegram_text(text or "(empty)"):
        messages.append(
            await bot.send_message(chat_id, chunk, parse_mode=parse_mode)
        )
    return messages
