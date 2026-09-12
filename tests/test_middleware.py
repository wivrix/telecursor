"""Tests for ALLOWED_USERS whitelist middleware."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiogram.types import Message, User

from config import Settings
from middleware import AuthWhitelistMiddleware


def _make_settings(tmp_path: Path, users: str) -> Settings:
    return Settings(
        bot_token="1234567890:TESTTOKEN_FOR_UNIT_TESTS",
        allowed_users=users,
        allowed_workspace_path=str(tmp_path),
        _env_file=None,  # type: ignore[call-arg]
    )


def _message(*, user_id: int, username: str | None) -> MagicMock:
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = username
    msg = MagicMock(spec=Message)
    msg.from_user = user
    msg.chat = SimpleNamespace(id=1)
    return msg


@pytest.mark.asyncio
async def test_middleware_allows_listed_user_id(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, "42,@alice")
    mw = AuthWhitelistMiddleware(settings)
    called = {"ok": False}

    async def handler(event: Any, data: dict[str, Any]) -> str:
        called["ok"] = True
        return "handled"

    result = await mw(handler, _message(user_id=42, username="bob"), {})
    assert result == "handled"
    assert called["ok"] is True


@pytest.mark.asyncio
async def test_middleware_allows_listed_username(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, "99,@alice")
    mw = AuthWhitelistMiddleware(settings)

    async def handler(event: Any, data: dict[str, Any]) -> str:
        return "ok"

    assert await mw(handler, _message(user_id=1, username="Alice"), {}) == "ok"


@pytest.mark.asyncio
async def test_middleware_drops_unauthorized(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, "42,@alice")
    mw = AuthWhitelistMiddleware(settings)
    called = False

    async def handler(event: Any, data: dict[str, Any]) -> str:
        nonlocal called
        called = True
        return "should-not-run"

    assert await mw(handler, _message(user_id=7, username="eve"), {}) is None
    assert called is False
