"""Auth whitelist middleware — drop unauthorized updates silently (logged)."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from config import Settings

logger = logging.getLogger(__name__)


class AuthWhitelistMiddleware(BaseMiddleware):
    """
    Allow only users listed in ALLOWED_USERS (by numeric id or username).

    Unauthorized payloads are dropped with no Telegram reply to avoid
    user-id / bot-existence enumeration.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = _extract_user(event)
        if user is None:
            logger.warning("Dropping update with no user: %s", type(event).__name__)
            return None

        if not self._settings.is_allowed_user(user.id, user.username):
            chat_id = None
            chat = getattr(event, "chat", None)
            if chat is not None:
                chat_id = getattr(chat, "id", None)
            elif isinstance(event, CallbackQuery) and event.message is not None:
                chat_id = event.message.chat.id
            logger.warning(
                "Unauthorized access attempt user_id=%s username=%r chat=%s type=%s",
                user.id,
                user.username,
                chat_id,
                type(event).__name__,
            )
            return None

        return await handler(event, data)


def _extract_user(event: TelegramObject) -> User | None:
    if isinstance(event, Message):
        return event.from_user
    if isinstance(event, CallbackQuery):
        return event.from_user
    user = getattr(event, "from_user", None)
    if isinstance(user, User):
        return user
    return None
