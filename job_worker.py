"""Per-chat job queue worker and agent run orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from io import BytesIO
from pathlib import Path

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from agent_runner import AgentRunner, save_telegram_file
from config import Settings
from cursor_info import classify_agent_error, fetch_usage, format_usage_message
from keyboards import approval_inline, main_keyboard
from session import ChatSession, QueuedJob, SessionStore
from streaming import StreamingTelegramSink, escape_html, send_long_message

logger = logging.getLogger(__name__)


def ensure_queue_worker(
    *,
    bot: Bot,
    settings: Settings,
    sessions: SessionStore,
    session: ChatSession,
) -> None:
    """Start a per-chat queue worker if one is not already running."""
    task = session.worker_task
    if task is not None and not task.done():
        return
    session.worker_task = asyncio.create_task(
        _queue_worker(
            bot=bot, settings=settings, sessions=sessions, session=session
        ),
        name=f"telecursor-queue-{session.chat_id}",
    )


async def clear_queue(session: ChatSession) -> int:
    """Cancel pending (not running) jobs and delete their temp attachments."""
    removed = 0
    async with session.jobs_lock:
        remaining: deque[QueuedJob] = deque()
        for job in session.jobs:
            if job.cancelled:
                continue
            job.cancelled = True
            removed += 1
            for path in job.attachments:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
        session.jobs = remaining
    return removed


async def _queue_worker(
    *,
    bot: Bot,
    settings: Settings,
    sessions: SessionStore,
    session: ChatSession,
) -> None:
    """Drain the per-chat job queue one task at a time.

    The in-memory queue is not persisted — pending jobs are lost on process restart.
    """
    try:
        while True:
            job: QueuedJob | None = None
            async with session.jobs_lock:
                while session.jobs:
                    candidate = session.jobs.popleft()
                    if candidate.cancelled:
                        for path in candidate.attachments:
                            try:
                                if path.exists():
                                    path.unlink()
                            except OSError:
                                pass
                        continue
                    job = candidate
                    session.current_job = job
                    break
                if job is None:
                    session.current_job = None
                    session.worker_task = None
                    return

            try:
                await run_agent(
                    bot=bot,
                    chat_id=session.chat_id,
                    settings=settings,
                    sessions=sessions,
                    session=session,
                    prompt=job.prompt,
                    attachments=job.attachments,
                )
            except Exception:
                logger.exception("Unhandled error during queued agent run %s", job.id)
                for path in job.attachments:
                    try:
                        if path.exists():
                            path.unlink()
                    except OSError:
                        pass
                try:
                    await bot.send_message(
                        session.chat_id,
                        f"💥 Internal error on job `{job.id}`. Check server logs.",
                        parse_mode="Markdown",
                    )
                except Exception:
                    logger.debug("Failed to notify chat of worker error", exc_info=True)
            finally:
                session.current_job = None
    except asyncio.CancelledError:
        session.worker_task = None
        session.current_job = None
        raise
    except Exception:
        logger.exception("Queue worker crashed for chat %s", session.chat_id)
        session.worker_task = None
        session.current_job = None
        # Self-heal: if jobs remain, start a fresh worker on the next tick
        if session.queued_count > 0:
            loop = asyncio.get_running_loop()
            loop.call_soon(
                lambda: ensure_queue_worker(
                    bot=bot, settings=settings, sessions=sessions, session=session
                )
            )


async def handle_approval_callback(
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


async def download_attachments(
    message: Message,
    bot: Bot,
    temp_dir: Path,
) -> list[Path]:
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


async def _typing_loop(bot: Bot, chat_id: int, stop: asyncio.Event) -> None:
    """Keep the Telegram 'typing…' indicator alive while the agent runs."""
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id, action="typing")
        except Exception:
            logger.debug("send_chat_action failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            continue


async def run_agent(
    *,
    bot: Bot,
    chat_id: int,
    settings: Settings,
    sessions: SessionStore,
    session: ChatSession,
    prompt: str,
    attachments: list[Path],
) -> None:
    sink = StreamingTelegramSink(
        bot,
        chat_id,
        edit_interval=settings.stream_edit_interval,
    )
    # No verbose banner — message appears when the first reply tokens arrive
    await sink.start(None)

    typing_stop = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_loop(bot, chat_id, typing_stop),
        name=f"typing-{chat_id}",
    )

    stderr_chunks: list[str] = []

    async def on_text(chunk: str) -> None:
        await sink.append(chunk)

    async def on_stderr(line: str) -> None:
        stderr_chunks.append(line)
        logger.debug("agent stderr: %s", line)

    async def on_approval_request(prompt_text: str, token: str) -> bool:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        session.pending_approvals[token] = fut
        try:
            safe_prompt = escape_html(prompt_text)
            await bot.send_message(
                chat_id,
                f"⚠️ <b>Agent approval required</b>\n\n<pre>{safe_prompt}</pre>",
                reply_markup=approval_inline(token),
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
        effort=session.effort,
        run_mode=session.run_mode.value,
        resume_session_id=session.agent_session_id,
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
        typing_stop.set()
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task

    if result.session_id:
        sessions.set_agent_session(chat_id, result.session_id)

    friendly = classify_agent_error(
        returncode=result.returncode,
        stdout=result.stdout_text,
        stderr=result.stderr_text,
        timed_out=result.timed_out,
        cancelled=result.cancelled,
        missing_binary=result.missing_binary,
    )

    remaining = session.queued_count
    queue_note = f"\n📥 {remaining} job(s) still queued." if remaining else ""

    if friendly:
        await sink.finalize()
        err_msg = friendly + queue_note
        await bot.send_message(chat_id, err_msg, reply_markup=main_keyboard())
        if "usage limit" in friendly.lower() or "rate limited" in friendly.lower():
            usage = await fetch_usage(settings.cursor_api_key)
            await bot.send_message(
                chat_id,
                format_usage_message(usage),
                parse_mode="Markdown",
            )
        return

    body = await sink.finalize()
    if not body:
        await bot.send_message(
            chat_id,
            "(no text response)" + queue_note,
            reply_markup=main_keyboard(),
        )
    elif queue_note:
        await bot.send_message(chat_id, queue_note.strip(), reply_markup=main_keyboard())

    if stderr_chunks and result.returncode != 0:
        err_text = "\n".join(stderr_chunks[-80:])
        if len(err_text) > 3500:
            err_text = "…\n" + err_text[-3500:]
        await send_long_message(bot, chat_id, f"stderr:\n{err_text}")
