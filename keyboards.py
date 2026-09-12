"""Telegram reply and inline keyboards."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from effort import EFFORT_LEVELS
from projects import list_projects
from session import AgentMode, RunMode

# Reply keyboard labels (main chat window)
BTN_MENU = "📋 Menu"
BTN_STATUS = "ℹ️ Status"
BTN_CLEAR = "🧹 Clear history"
BTN_REFRESH = "🔄 Refresh"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MENU), KeyboardButton(text=BTN_STATUS)],
            [KeyboardButton(text=BTN_CLEAR), KeyboardButton(text=BTN_REFRESH)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📁 Projects", callback_data="menu:projects"),
                InlineKeyboardButton(text="🧭 Run mode", callback_data="menu:runmode"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Approvals", callback_data="menu:mode"),
                InlineKeyboardButton(text="🧠 Model", callback_data="menu:model"),
            ],
            [
                InlineKeyboardButton(text="💪 Effort", callback_data="menu:effort"),
                InlineKeyboardButton(text="📂 Path", callback_data="menu:workspace"),
            ],
            [
                InlineKeyboardButton(text="📊 Limit", callback_data="menu:limit"),
                InlineKeyboardButton(text="📥 Queue", callback_data="menu:queue"),
            ],
            [
                InlineKeyboardButton(text="🛑 Stop", callback_data="menu:stop"),
                InlineKeyboardButton(text="🩺 Agent health", callback_data="menu:health"),
            ],
            [InlineKeyboardButton(text="❓ Help", callback_data="menu:help")],
        ]
    )


def mode_inline(current: AgentMode) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("✅ " if current is AgentMode.SAFE else "") + "Safe",
                    callback_data="setmode:safe",
                ),
                InlineKeyboardButton(
                    text=("✅ " if current is AgentMode.YOLO else "") + "Yolo",
                    callback_data="setmode:yolo",
                ),
            ]
        ]
    )


def run_mode_inline(current: RunMode) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for mode in (RunMode.AGENT, RunMode.PLAN, RunMode.ASK):
        prefix = "✅ " if current is mode else ""
        row.append(
            InlineKeyboardButton(
                text=f"{prefix}{mode.value}",
                callback_data=f"setrunmode:{mode.value}",
            )
        )
    rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def projects_inline(current_id: str | None) -> InlineKeyboardMarkup:
    projects = list_projects()
    rows: list[list[InlineKeyboardButton]] = []
    if not projects:
        rows.append(
            [
                InlineKeyboardButton(
                    text="No projects — run telecursor start -d",
                    callback_data="menu:help",
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)
    for proj in projects[:40]:
        prefix = "✅ " if proj.id == current_id else ""
        label = f"{prefix}{proj.name}"[:60]
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"setproject:{proj.id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def effort_inline(current: str | None) -> InlineKeyboardMarkup:
    levels = [("auto", None), *[(level, level) for level in EFFORT_LEVELS]]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for label, value in levels:
        selected = (current is None and value is None) or (current == value)
        prefix = "✅ " if selected else ""
        cb = f"seteffort:{value or 'auto'}"
        row.append(InlineKeyboardButton(text=f"{prefix}{label}", callback_data=cb))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def approval_inline(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve", callback_data=f"approve:{token}"
                ),
                InlineKeyboardButton(
                    text="❌ Reject", callback_data=f"reject:{token}"
                ),
            ]
        ]
    )
