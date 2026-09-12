"""Telegram command and message handlers."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Settings, validate_workspace
from cursor_info import (
    check_agent_health,
    fetch_usage,
    format_usage_message,
    list_models,
)
from effort import normalize_effort
from job_worker import (
    clear_queue,
    download_attachments,
    ensure_queue_worker,
    handle_approval_callback,
)
from keyboards import (
    BTN_CLEAR,
    BTN_MENU,
    BTN_REFRESH,
    BTN_STATUS,
    effort_inline,
    main_keyboard,
    menu_inline,
    mode_inline,
    projects_inline,
    run_mode_inline,
)
from projects import get_project, list_projects
from session import AgentMode, ChatSession, QueuedJob, RunMode, SessionStore
from streaming import escape_md, send_long_message

logger = logging.getLogger(__name__)


def build_router(settings: Settings, sessions: SessionStore) -> Router:
    router = Router(name="cursor_bot")

    def session_summary(session: ChatSession) -> str:
        busy = session.is_busy
        queued = session.queued_count
        current = session.current_job.preview if session.current_job else "—"
        # Paths/ids sit in `code` spans (safe). Free-text previews need escaping.
        return (
            f"Project: `{session.project_label}`\n"
            f"Path: `{session.workspace}`\n"
            f"Run mode: `{session.run_mode_label}`\n"
            f"Approvals: `{session.mode.value}`\n"
            f"Model: `{session.model_label}`\n"
            f"Effort: `{session.effort_label}`\n"
            f"History: `{session.history_label}`\n"
            f"Busy: `{busy}`\n"
            f"Current: `{current}`\n"
            f"Queued: `{queued}`\n"
            f"Agent bin: `{settings.agent_bin}`"
        )

    help_text = (
        "🔐 *Telecursor*\n\n"
        "Send a *text prompt*, *photo*, or *document*.\n"
        "History is kept until *Clear history*.\n\n"
        "*Commands*\n"
        "/menu — control panel\n"
        "/projects — pick a registered project\n"
        "/runmode `agent|plan|ask` — Cursor run mode\n"
        "/mode `safe|yolo` — tool approvals\n"
        "/model `/models` `/effort` — model controls\n"
        "/workspace `<path>` — path inside the project\n"
        "/refresh — reset to default project path + clear history\n"
        "/clear — clear conversation history\n"
        "/limit `/status` `/queue` `/stop` `/health`"
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
        health = await check_agent_health(settings.agent_bin, force_refresh=True)
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
                "Tool approvals:",
                reply_markup=mode_inline(session.mode),
            )
            return
        mode = AgentMode.YOLO if arg == "yolo" else AgentMode.SAFE
        sessions.set_mode(message.chat.id, mode)
        flag = "auto-approve (`--force`)" if mode is AgentMode.YOLO else "Approve/Reject prompts"
        await message.answer(
            f"Approvals set to *{mode.value}* — {flag}.",
            parse_mode="Markdown",
        )

    @router.message(Command("runmode"))
    async def cmd_runmode(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip().lower()
        session = sessions.get(message.chat.id)
        if arg not in {"agent", "plan", "ask"}:
            await message.answer(
                f"Current run mode: `{session.run_mode_label}`\n"
                "agent = full tools · plan = planning · ask = Q&A (read-only)",
                parse_mode="Markdown",
                reply_markup=run_mode_inline(session.run_mode),
            )
            return
        run_mode = RunMode(arg)
        sessions.set_run_mode(message.chat.id, run_mode)
        await message.answer(
            f"Run mode set to `{run_mode.value}`.",
            parse_mode="Markdown",
        )

    @router.message(Command("projects", "project"))
    async def cmd_projects(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip()
        session = sessions.get(message.chat.id)
        if not arg:
            projects = list_projects()
            if not projects:
                await message.answer(
                    "No projects yet.\n"
                    "On the server:\n"
                    "`cd /path/to/project && telecursor start -d`",
                    parse_mode="Markdown",
                )
                return
            await message.answer(
                f"Current project: `{session.project_label}`\nSelect one:",
                parse_mode="Markdown",
                reply_markup=projects_inline(session.project_id),
            )
            return
        proj = get_project(arg)
        if proj is None:
            # try match by name
            matches = [p for p in list_projects() if p.name.lower() == arg.lower()]
            proj = matches[0] if len(matches) == 1 else None
        if proj is None:
            await message.answer(
                f"❌ Unknown project `{arg}`.\nUse /projects to pick one.",
                parse_mode="Markdown",
                reply_markup=projects_inline(session.project_id),
            )
            return
        sessions.set_project(
            message.chat.id, project_id=proj.id, workspace=proj.root
        )
        await message.answer(
            f"📁 Project set to *{escape_md(proj.name)}*\n`{proj.path}`\n(History cleared.)",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
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

    @router.message(Command("effort"))
    async def cmd_effort(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip().lower()
        session = sessions.get(message.chat.id)
        if not arg:
            await message.answer(
                f"Current effort: `{session.effort_label}`\n"
                "Pick a level (applied as `model[effort=…]`; needs a concrete `/model`):",
                parse_mode="Markdown",
                reply_markup=effort_inline(session.effort),
            )
            return
        try:
            effort = normalize_effort(arg)
        except ValueError as exc:
            await message.answer(
                f"❌ {exc}",
                reply_markup=effort_inline(session.effort),
            )
            return
        sessions.set_effort(message.chat.id, effort)
        note = ""
        if effort and session.model is None:
            note = (
                "\n⚠️ Effort applies when a specific model is set "
                "(`auto` cannot take effort brackets)."
            )
        await message.answer(
            f"Effort set to `{effort or 'auto'}`.{note}",
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
            lines.append(f"• `{mid}` — {escape_md(name)}")
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
        session = sessions.get(message.chat.id)
        if not raw:
            await message.answer(
                f"Current path: `{session.workspace}`\n"
                f"Usage: `/workspace <path>` (inside the selected project)",
                parse_mode="Markdown",
            )
            return
        roots = [p.root for p in list_projects()]
        project_root = session.workspace
        if session.project_id:
            proj = get_project(session.project_id)
            if proj is not None:
                project_root = proj.root
        if project_root not in roots:
            roots.append(project_root)
        if session.workspace not in roots:
            roots.append(session.workspace)
        try:
            path = validate_workspace(
                raw,
                project_root,
                extra_roots=roots,
            )
        except ValueError as exc:
            await message.answer(f"❌ Invalid path: {exc}")
            return
        sessions.set_workspace(message.chat.id, path)
        await message.answer(
            f"Path set to `{path}`\n(History cleared.)",
            parse_mode="Markdown",
        )

    @router.message(Command("clear", "clearhistory"))
    async def cmd_clear(message: Message) -> None:
        sessions.clear_history(message.chat.id)
        await message.answer(
            "🧹 Agent history cleared. The next prompt starts a fresh chat.",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("refresh"))
    async def cmd_refresh(message: Message) -> None:
        """Reset path to the selected/default project root and clear history."""
        session = sessions.get(message.chat.id)
        workspace = settings.default_workspace_path
        assert workspace is not None
        proj = get_project(session.project_id) if session.project_id else None
        if proj is None:
            from projects import find_project_for_path

            proj = find_project_for_path(workspace)
        if proj is not None:
            session = sessions.set_project(
                message.chat.id, project_id=proj.id, workspace=proj.root
            )
        else:
            session = sessions.refresh_workspace(message.chat.id, workspace)
        async with session.jobs_lock:
            dropped = 0
            while session.jobs:
                job = session.jobs.popleft()
                dropped += 1
                for path in job.attachments:
                    try:
                        if path.exists():
                            path.unlink()
                    except OSError:
                        pass
        await message.answer(
            "🔄 Refreshed.\n"
            f"Project: `{session.project_label}`\n"
            f"Path: `{session.workspace}`\n"
            f"History: cleared"
            + (f"\nDropped `{dropped}` queued job(s)." if dropped else ""),
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("cancel", "stop"))
    async def cmd_cancel(message: Message) -> None:
        session = sessions.get(message.chat.id)
        runner = session.active_runner
        if runner is None and session.current_job is None:
            queued = session.queued_count
            if queued:
                await message.answer(
                    f"No active run. {queued} job(s) waiting — "
                    "use `/queue clear` to drop them.",
                    parse_mode="Markdown",
                )
            else:
                await message.answer("No active agent run.")
            return
        if runner is not None:
            await runner.cancel()
        await message.answer(
            "🛑 Stopping the current task…\n"
            "Queued jobs will continue afterward (or `/queue clear` to drop them)."
        )

    @router.message(Command("queue"))
    async def cmd_queue(message: Message, command: CommandObject) -> None:
        session = sessions.get(message.chat.id)
        arg = (command.args or "").strip().lower()
        if arg in {"clear", "flush", "empty"}:
            removed = await clear_queue(session)
            await message.answer(
                f"🧹 Cleared {removed} queued job(s). "
                "The current run (if any) was left alone — use /stop to cancel it."
            )
            return

        lines = ["📥 *Job queue*", ""]
        if session.current_job and not session.current_job.cancelled:
            lines.append(
                f"*Running:* `{session.current_job.id}` — "
                f"{escape_md(session.current_job.preview)}"
            )
        else:
            lines.append("*Running:* _(none)_")

        pending = session.queue_snapshot()
        if not pending:
            lines.append("*Queued:* _(empty)_")
        else:
            lines.append(f"*Queued ({len(pending)}):*")
            for i, job in enumerate(pending, start=1):
                lines.append(f"{i}. `{job.id}` — {escape_md(job.preview)}")
        lines.append("\n`/stop` — stop current · `/queue clear` — drop pending")
        await message.answer("\n".join(lines), parse_mode="Markdown")

    # --- Reply keyboard shortcuts ---
    @router.message(F.text == BTN_MENU)
    async def kb_menu(message: Message) -> None:
        await cmd_menu(message)

    @router.message(F.text == BTN_STATUS)
    async def kb_status(message: Message) -> None:
        await cmd_status(message)

    @router.message(F.text == BTN_CLEAR)
    async def kb_clear(message: Message) -> None:
        await cmd_clear(message)

    @router.message(F.text == BTN_REFRESH)
    async def kb_refresh(message: Message) -> None:
        await cmd_refresh(message)

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
                "Tool approvals:", reply_markup=mode_inline(session.mode)
            )
        elif action == "runmode":
            await callback.message.answer(
                f"Current run mode: `{session.run_mode_label}`",
                parse_mode="Markdown",
                reply_markup=run_mode_inline(session.run_mode),
            )
        elif action == "projects":
            await callback.message.answer(
                f"Current project: `{session.project_label}`",
                parse_mode="Markdown",
                reply_markup=projects_inline(session.project_id),
            )
        elif action == "model":
            await callback.message.answer(
                f"Current model: `{session.model_label}`\nSend `/model <id>` or /models",
                parse_mode="Markdown",
            )
        elif action == "effort":
            await callback.message.answer(
                f"Current effort: `{session.effort_label}`",
                parse_mode="Markdown",
                reply_markup=effort_inline(session.effort),
            )
        elif action == "clear":
            sessions.clear_history(callback.message.chat.id)
            await callback.message.answer(
                "🧹 Agent history cleared. The next prompt starts a fresh chat.",
                reply_markup=main_keyboard(),
            )
        elif action == "refresh":
            workspace = settings.default_workspace_path
            assert workspace is not None
            proj = get_project(session.project_id) if session.project_id else None
            if proj is None:
                from projects import find_project_for_path

                proj = find_project_for_path(workspace)
            if proj is not None:
                session = sessions.set_project(
                    callback.message.chat.id,
                    project_id=proj.id,
                    workspace=proj.root,
                )
            else:
                session = sessions.refresh_workspace(
                    callback.message.chat.id, workspace
                )
            async with session.jobs_lock:
                while session.jobs:
                    job = session.jobs.popleft()
                    for path in job.attachments:
                        try:
                            if path.exists():
                                path.unlink()
                        except OSError:
                            pass
            await callback.message.answer(
                "🔄 Refreshed.\n"
                f"Project: `{session.project_label}`\n"
                f"Path: `{session.workspace}`\n"
                "History: cleared",
                parse_mode="Markdown",
                reply_markup=main_keyboard(),
            )
        elif action == "workspace":
            await callback.message.answer(
                f"Current: `{session.workspace}`\n"
                f"Send `/workspace <path>` inside the selected project",
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
        elif action == "queue":
            await cmd_queue(
                callback.message, CommandObject(command="queue", args=None)
            )
        elif action == "stop":
            # Reuse cancel logic against the callback's chat
            runner = session.active_runner
            if runner is None and session.current_job is None:
                await callback.message.answer("No active agent run.")
            else:
                if runner is not None:
                    await runner.cancel()
                await callback.message.answer(
                    "🛑 Stopping the current task…\n"
                    "Queued jobs will continue afterward "
                    "(or `/queue clear` to drop them)."
                )
        elif action == "health":
            health = await check_agent_health(settings.agent_bin, force_refresh=True)
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
            f"Approvals set to *{mode.value}*.", parse_mode="Markdown"
        )
        await callback.answer(f"Mode: {mode.value}")

    @router.callback_query(F.data.startswith("setrunmode:"))
    async def on_set_run_mode(callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            await callback.answer()
            return
        raw = callback.data.split(":", 1)[1]
        if raw not in {"agent", "plan", "ask"}:
            await callback.answer("Invalid", show_alert=True)
            return
        run_mode = RunMode(raw)
        sessions.set_run_mode(callback.message.chat.id, run_mode)
        await callback.message.answer(
            f"Run mode set to `{run_mode.value}`.",
            parse_mode="Markdown",
        )
        await callback.answer(f"Run mode: {run_mode.value}")

    @router.callback_query(F.data.startswith("setproject:"))
    async def on_set_project(callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            await callback.answer()
            return
        pid = callback.data.split(":", 1)[1]
        proj = get_project(pid)
        if proj is None:
            await callback.answer("Unknown project", show_alert=True)
            return
        sessions.set_project(
            callback.message.chat.id, project_id=proj.id, workspace=proj.root
        )
        await callback.message.answer(
            f"📁 Project set to *{escape_md(proj.name)}*\n`{proj.path}`\n(History cleared.)",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        await callback.answer(proj.name)

    @router.callback_query(F.data.startswith("seteffort:"))
    async def on_set_effort(callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            await callback.answer()
            return
        raw = callback.data.split(":", 1)[1]
        try:
            effort = normalize_effort(raw)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        session = sessions.set_effort(callback.message.chat.id, effort)
        note = ""
        if effort and session.model is None:
            note = " (set a `/model` for it to apply)"
        await callback.message.answer(
            f"Effort set to `{effort or 'auto'}`{note}.",
            parse_mode="Markdown",
        )
        await callback.answer()

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
        await handle_approval_callback(callback, sessions, approved=True)

    @router.callback_query(F.data.startswith("reject:"))
    async def on_reject(callback: CallbackQuery) -> None:
        await handle_approval_callback(callback, sessions, approved=False)

    @router.message(F.photo | F.document | F.text)
    async def on_prompt(message: Message, bot: Bot) -> None:
        keyboard_labels = {
            BTN_MENU,
            BTN_STATUS,
            BTN_CLEAR,
            BTN_REFRESH,
        }
        if message.text and (
            message.text.startswith("/") or message.text in keyboard_labels
        ):
            return

        session = sessions.get(message.chat.id)

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
                "Or: `telecursor config --api-key YOUR_KEY`"
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
            attachments = await download_attachments(
                message, bot, settings.temp_upload_dir
            )
        except Exception:
            logger.exception("Failed to download attachments")
            await message.answer("❌ Failed to download attachment(s).")
            return

        job = QueuedJob.create(
            chat_id=message.chat.id,
            prompt=prompt_text,
            attachments=attachments,
        )

        async with session.jobs_lock:
            pending = session.queued_count
            if pending >= settings.max_queue_size:
                for path in attachments:
                    try:
                        if path.exists():
                            path.unlink()
                    except OSError:
                        pass
                await message.answer(
                    f"⛔ Queue is full ({settings.max_queue_size} waiting). "
                    "Wait for jobs to finish, or `/queue clear` / `/stop`."
                )
                return
            was_busy = session.is_busy or pending > 0
            session.jobs.append(job)
            position = session.queued_count

        # Start the worker before any Telegram I/O so a failed answer()
        # cannot leave jobs stranded with no consumer.
        ensure_queue_worker(
            bot=bot, settings=settings, sessions=sessions, session=session
        )

        try:
            if was_busy:
                await message.answer(
                    f"📥 Queued at position *{position}* (`{job.id}`).\n"
                    f"_{escape_md(job.preview)}_\n\n"
                    "I'll start it when the current task finishes.\n"
                    "`/queue` · `/stop` (current) · `/queue clear`",
                    parse_mode="Markdown",
                    reply_markup=main_keyboard(),
                )
            else:
                await message.answer(
                    f"⏳ Starting (`{job.id}`)…",
                    parse_mode="Markdown",
                    reply_markup=main_keyboard(),
                )
        except Exception:
            logger.exception("Failed to ack queued job %s", job.id)

    return router

