"""Telegram command and message handlers."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from agent_runner import AgentRunner, save_telegram_file
from config import Settings, validate_workspace
from cursor_info import (
    check_agent_health,
    classify_agent_error,
    fetch_usage,
    format_usage_message,
    list_models,
)
from session import AgentMode, ChatSession, SessionStore
from streaming import StreamingTelegramSink, send_long_message

logger = logging.getLogger(__name__)

# Reply keyboard labels
BTN_MENU = "📋 Menu"
BTN_STATUS = "ℹ️ Status"
BTN_MODE = "⚙️ Mode"
BTN_MODEL = "🧠 Model"
BTN_LIMIT = "📊 Limit"
BTN_CANCEL = "🛑 Cancel"
BTN_HELP = "❓ Help"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MENU), KeyboardButton(text=BTN_STATUS)],
            [KeyboardButton(text=BTN_MODE), KeyboardButton(text=BTN_MODEL)],
            [KeyboardButton(text=BTN_LIMIT), KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚙️ Mode", callback_data="menu:mode"),
                InlineKeyboardButton(text="🧠 Model", callback_data="menu:model"),
            ],
            [
                InlineKeyboardButton(text="📁 Workspace", callback_data="menu:workspace"),
                InlineKeyboardButton(text="📊 Limit", callback_data="menu:limit"),
            ],
            [
                InlineKeyboardButton(text="ℹ️ Status", callback_data="menu:status"),
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


def build_router(settings: Settings, sessions: SessionStore) -> Router:
    router = Router(name="cursor_bot")

    def session_summary(session: ChatSession) -> str:
        busy = session.active_runner is not None
        return (
            f"Mode: `{session.mode.value}`\n"
            f"Model: `{session.model_label}`\n"
            f"Workspace: `{session.workspace}`\n"
            f"Busy: `{busy}`\n"
            f"Jail: `{settings.allowed_workspace_path}`\n"
            f"Agent bin: `{settings.agent_bin}`"
        )

    help_text = (
        "🔐 *Cursor Agent Telegram Bridge*\n\n"
        "Send a *text prompt*, *photo*, or *document* to run the local agent.\n"
        "I stream progress and notify you when the run completes.\n\n"
        "*Commands*\n"
        "/menu — control panel\n"
        "/mode `safe|yolo` — tool approval mode\n"
        "/model `<id>` — set model (`auto` to clear)\n"
        "/models — list available models\n"
        "/workspace `<path>` — set cwd (jailed)\n"
        "/limit — remaining Cursor usage\n"
        "/status — session + agent state\n"
        "/health — agent install / login check\n"
        "/cancel — stop the active run\n"
        "/help — this message"
    )

    @router.message(Command("start", "help"))
    async def cmd_help(message: Message) -> None:
        session = sessions.get(message.chat.id)
        await message.answer(
            f"{help_text}\n\n{session_summary(session)}",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("menu"))
    async def cmd_menu(message: Message) -> None:
        await message.answer(
            "Control panel — pick an option:",
            reply_markup=menu_inline(),
        )

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        session = sessions.get(message.chat.id)
        await message.answer(
            session_summary(session),
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("health"))
    async def cmd_health(message: Message) -> None:
        await message.answer("🩺 Checking agent…")
        health = await check_agent_health(settings.agent_bin)
        if not health.installed:
            await message.answer(f"❌ {health.error}")
            return
        lines = [
            f"✅ Agent found: `{health.path}`",
            f"Authenticated: `{'yes' if health.authenticated else 'no'}`",
        ]
        if health.email:
            lines.append(f"Account: `{health.email}`")
        if health.subscription:
            lines.append(f"Plan: `{health.subscription}`")
        if health.error:
            lines.append(f"\n⚠️ {health.error}")
        await message.answer("\n".join(lines), parse_mode="Markdown")

    @router.message(Command("limit", "usage"))
    async def cmd_limit(message: Message) -> None:
        await message.answer("📊 Fetching usage…")
        usage = await fetch_usage(settings.cursor_api_key)
        await message.answer(format_usage_message(usage), parse_mode="Markdown")

    @router.message(Command("mode"))
    async def cmd_mode(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip().lower()
        session = sessions.get(message.chat.id)
        if arg not in {"yolo", "safe"}:
            await message.answer(
                "Choose a mode:",
                reply_markup=mode_inline(session.mode),
            )
            return
        mode = AgentMode.YOLO if arg == "yolo" else AgentMode.SAFE
        sessions.set_mode(message.chat.id, mode)
        flag = "auto-approve (`--force`)" if mode is AgentMode.YOLO else "Approve/Reject prompts"
        await message.answer(
            f"Mode set to *{mode.value}* — {flag}.",
            parse_mode="Markdown",
        )

    @router.message(Command("model"))
    async def cmd_model(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip()
        session = sessions.get(message.chat.id)
        if not arg:
            await message.answer(
                f"Current model: `{session.model_label}`\n"
                "Usage: `/model composer-2.5` or `/model auto`\n"
                "See `/models` for the full list.",
                parse_mode="Markdown",
            )
            return
        model = None if arg.lower() in {"auto", "default", "none"} else arg
        sessions.set_model(message.chat.id, model)
        await message.answer(
            f"Model set to `{model or 'auto'}`.",
            parse_mode="Markdown",
        )

    @router.message(Command("models"))
    async def cmd_models(message: Message) -> None:
        await message.answer("🧠 Loading models…")
        try:
            models = await list_models(settings.agent_bin)
        except Exception as exc:  # noqa: BLE001
            await message.answer(f"❌ Could not list models: {exc}")
            return
        if not models:
            await message.answer("No models returned.")
            return
        # Show first page as text + popular inline shortcuts
        lines = ["*Available models* (use `/model <id>`):\n"]
        for mid, name in models[:40]:
            lines.append(f"• `{mid}` — {name}")
        if len(models) > 40:
            lines.append(f"\n…and {len(models) - 40} more.")
        await send_long_message(
            message.bot, message.chat.id, "\n".join(lines), parse_mode="Markdown"
        )

        popular = ["auto", "composer-2.5", "composer-2.5-fast"]
        available_ids = {m[0] for m in models} | {"auto"}
        row = [
            InlineKeyboardButton(text=p, callback_data=f"setmodel:{p}")
            for p in popular
            if p in available_ids or p == "auto"
        ]
        if row:
            await message.answer(
                "Quick select:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[row]),
            )

    @router.message(Command("workspace"))
    async def cmd_workspace(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        if not raw:
            session = sessions.get(message.chat.id)
            await message.answer(
                f"Current workspace: `{session.workspace}`\n"
                f"Usage: `/workspace <path>` "
                f"(must be under `{settings.allowed_workspace_path}`)",
                parse_mode="Markdown",
            )
            return
        try:
            path = validate_workspace(raw, settings.allowed_workspace_path)
        except ValueError as exc:
            await message.answer(f"❌ Invalid workspace: {exc}")
            return
        sessions.set_workspace(message.chat.id, path)
        await message.answer(f"Workspace set to `{path}`", parse_mode="Markdown")

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message) -> None:
        session = sessions.get(message.chat.id)
        runner = session.active_runner
        if runner is None:
            await message.answer("No active agent run.")
            return
        await runner.cancel()
        await message.answer("🛑 Stopping the agent…")

    # --- Reply keyboard shortcuts ---
    @router.message(F.text == BTN_MENU)
    async def kb_menu(message: Message) -> None:
        await cmd_menu(message)

    @router.message(F.text == BTN_STATUS)
    async def kb_status(message: Message) -> None:
        await cmd_status(message)

    @router.message(F.text == BTN_MODE)
    async def kb_mode(message: Message) -> None:
        session = sessions.get(message.chat.id)
        await message.answer("Choose a mode:", reply_markup=mode_inline(session.mode))

    @router.message(F.text == BTN_MODEL)
    async def kb_model(message: Message) -> None:
        await cmd_models(message)

    @router.message(F.text == BTN_LIMIT)
    async def kb_limit(message: Message) -> None:
        await cmd_limit(message)

    @router.message(F.text == BTN_CANCEL)
    async def kb_cancel(message: Message) -> None:
        await cmd_cancel(message)

    @router.message(F.text == BTN_HELP)
    async def kb_help(message: Message) -> None:
        await cmd_help(message)

    # --- Inline callbacks ---
    @router.callback_query(F.data.startswith("menu:"))
    async def on_menu(callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            await callback.answer()
            return
        action = callback.data.split(":", 1)[1]
        session = sessions.get(callback.message.chat.id)
        if action == "mode":
            await callback.message.answer(
                "Choose a mode:", reply_markup=mode_inline(session.mode)
            )
        elif action == "model":
            await callback.message.answer(
                f"Current model: `{session.model_label}`\nSend `/model <id>` or /models",
                parse_mode="Markdown",
            )
        elif action == "workspace":
            await callback.message.answer(
                f"Current: `{session.workspace}`\n"
                f"Send `/workspace <path>` under `{settings.allowed_workspace_path}`",
                parse_mode="Markdown",
            )
        elif action == "limit":
            usage = await fetch_usage(settings.cursor_api_key)
            await callback.message.answer(
                format_usage_message(usage), parse_mode="Markdown"
            )
        elif action == "status":
            await callback.message.answer(
                session_summary(session), parse_mode="Markdown"
            )
        elif action == "health":
            health = await check_agent_health(settings.agent_bin)
            msg = health.error or (
                f"✅ `{health.path}`\n"
                f"Auth: {'yes' if health.authenticated else 'no'}\n"
                f"Account: {health.email or '—'}\n"
                f"Plan: {health.subscription or '—'}"
            )
            await callback.message.answer(msg, parse_mode="Markdown")
        elif action == "help":
            await callback.message.answer(help_text, parse_mode="Markdown")
        await callback.answer()

    @router.callback_query(F.data.startswith("setmode:"))
    async def on_set_mode(callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            await callback.answer()
            return
        mode_raw = callback.data.split(":", 1)[1]
        if mode_raw not in {"safe", "yolo"}:
            await callback.answer("Invalid", show_alert=True)
            return
        mode = AgentMode(mode_raw)
        sessions.set_mode(callback.message.chat.id, mode)
        await callback.message.answer(
            f"Mode set to *{mode.value}*.", parse_mode="Markdown"
        )
        await callback.answer(f"Mode: {mode.value}")

    @router.callback_query(F.data.startswith("setmodel:"))
    async def on_set_model(callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            await callback.answer()
            return
        model_raw = callback.data.split(":", 1)[1]
        model = None if model_raw.lower() in {"auto", "default"} else model_raw
        sessions.set_model(callback.message.chat.id, model)
        await callback.message.answer(
            f"Model set to `{model or 'auto'}`.", parse_mode="Markdown"
        )
        await callback.answer("Model updated")

    @router.callback_query(F.data.startswith("approve:"))
    async def on_approve(callback: CallbackQuery) -> None:
        await _handle_approval_callback(callback, sessions, approved=True)

    @router.callback_query(F.data.startswith("reject:"))
    async def on_reject(callback: CallbackQuery) -> None:
        await _handle_approval_callback(callback, sessions, approved=False)

    @router.message(F.photo | F.document | F.text)
    async def on_prompt(message: Message, bot: Bot) -> None:
        if message.text and (
            message.text.startswith("/")
            or message.text
            in {BTN_MENU, BTN_STATUS, BTN_MODE, BTN_MODEL, BTN_LIMIT, BTN_CANCEL, BTN_HELP}
        ):
            return

        session = sessions.get(message.chat.id)
        if session.run_lock.locked() or session.active_runner is not None:
            await message.answer("⏳ Agent is already running. Use /cancel or wait.")
            return

        # Preflight: agent installed
        health = await check_agent_health(settings.agent_bin)
        if not health.installed:
            await message.answer(
                "❌ Agent not installed.\n"
                f"{health.error}\n\n"
                "Fix on the server, then `/health`."
            )
            return
        if not health.authenticated and not settings.cursor_api_key:
            await message.answer(
                "🔐 Agent is not logged in and no CURSOR_API_KEY is set.\n"
                "On the server run: `agent login`\n"
                "Or: `python main.py config --api-key YOUR_KEY`"
            )
            return

        prompt_text = (message.text or message.caption or "").strip()
        if not prompt_text and not (message.photo or message.document):
            await message.answer(
                "Send a text prompt, or a file/photo with an optional caption.",
                reply_markup=main_keyboard(),
            )
            return
        if not prompt_text:
            prompt_text = "Analyze the attached file(s) and summarize findings."

        attachments: list[Path] = []
        try:
            async with session.run_lock:
                attachments = await _download_attachments(
                    message, bot, settings.temp_upload_dir
                )
                await _run_agent(
                    bot=bot,
                    message=message,
                    settings=settings,
                    session=session,
                    prompt=prompt_text,
                    attachments=attachments,
                )
        except Exception:
            logger.exception("Unhandled error during agent run")
            for path in attachments:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
            await message.answer(
                "💥 Internal error while running the agent. Check server logs."
            )

    return router


async def _handle_approval_callback(
    callback: CallbackQuery,
    sessions: SessionStore,
    *,
    approved: bool,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    try:
        _, token = callback.data.split(":", 1)
    except ValueError:
        await callback.answer("Invalid callback", show_alert=True)
        return

    session = sessions.get(callback.message.chat.id)
    fut = session.pending_approvals.get(token)

    if fut is None or fut.done():
        await callback.answer("No pending approval", show_alert=True)
        return

    fut.set_result(approved)

    label = "✅ Approved" if approved else "❌ Rejected"
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"{label} by user")
    except Exception:
        logger.debug("Could not update approval message UI", exc_info=True)
    await callback.answer(label)


async def _download_attachments(
    message: Message,
    bot: Bot,
    temp_dir: Path,
) -> list[Path]:
    from io import BytesIO

    paths: list[Path] = []

    if message.photo:
        photo = message.photo[-1]
        buf = BytesIO()
        await bot.download(photo, destination=buf)
        path = await save_telegram_file(
            temp_dir, file_bytes=buf.getvalue(), suggested_name="photo.jpg"
        )
        paths.append(path)

    if message.document:
        doc = message.document
        suggested = doc.file_name or "document.bin"
        buf = BytesIO()
        await bot.download(doc, destination=buf)
        path = await save_telegram_file(
            temp_dir, file_bytes=buf.getvalue(), suggested_name=suggested
        )
        paths.append(path)

    return paths


async def _run_agent(
    *,
    bot: Bot,
    message: Message,
    settings: Settings,
    session: ChatSession,
    prompt: str,
    attachments: list[Path],
) -> None:
    chat_id = message.chat.id
    sink = StreamingTelegramSink(
        bot,
        chat_id,
        edit_interval=settings.stream_edit_interval,
    )
    await sink.start(
        f"⏳ Running agent…\nmodel=`{session.model_label}` mode=`{session.mode.value}`"
    )

    stderr_chunks: list[str] = []

    async def on_text(chunk: str) -> None:
        await sink.append(chunk)

    async def on_stderr(line: str) -> None:
        stderr_chunks.append(line)
        logger.debug("agent stderr: %s", line)

    async def on_approval_request(prompt_text: str, token: str) -> bool:
        keyboard = InlineKeyboardMarkup(
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
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        session.pending_approvals[token] = fut
        try:
            safe_prompt = prompt_text.replace("<", "&lt;").replace(">", "&gt;")
            await bot.send_message(
                chat_id,
                f"⚠️ <b>Agent approval required</b>\n\n<pre>{safe_prompt}</pre>",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            try:
                return await asyncio.wait_for(fut, timeout=300.0)
            except asyncio.TimeoutError:
                await bot.send_message(chat_id, "⌛ Approval timed out; rejecting.")
                return False
        finally:
            session.pending_approvals.pop(token, None)

    runner = AgentRunner(
        settings=settings,
        workspace=session.workspace,
        force=session.force,
        prompt=prompt,
        model=session.model,
        attachment_paths=list(attachments),
        on_text=on_text,
        on_stderr=on_stderr,
        on_approval_request=None if session.force else on_approval_request,
    )
    session.active_runner = runner
    try:
        result = await runner.run()
    finally:
        session.active_runner = None

    friendly = classify_agent_error(
        returncode=result.returncode,
        stdout=result.stdout_text,
        stderr=result.stderr_text,
        timed_out=result.timed_out,
        cancelled=result.cancelled,
        missing_binary=result.missing_binary,
    )

    if friendly:
        await sink.finalize(f"\n\n———\n{friendly}")
        # If limit exceeded, also push /limit tip
        if "usage limit" in friendly.lower() or "rate limited" in friendly.lower():
            usage = await fetch_usage(settings.cursor_api_key)
            await bot.send_message(
                chat_id,
                format_usage_message(usage),
                parse_mode="Markdown",
            )
        await bot.send_message(chat_id, "❌ Run finished with an error.")
        return

    footer = (
        f"\n\n———\n✅ Completed (exit `{result.returncode}`) · "
        f"model `{session.model_label}` · mode `{session.mode.value}`"
    )
    await sink.finalize(footer)
    await bot.send_message(
        chat_id,
        "✅ Agent run completed.",
        reply_markup=main_keyboard(),
    )

    # Only show stderr on failure-ish noise when present and non-empty with warnings
    if stderr_chunks and result.returncode != 0:
        err_text = "\n".join(stderr_chunks[-80:])
        if len(err_text) > 3500:
            err_text = "…\n" + err_text[-3500:]
        await send_long_message(bot, chat_id, f"stderr:\n{err_text}")
