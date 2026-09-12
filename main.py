#!/usr/bin/env python3
"""Telegram ↔ cursor-agent bridge — CLI entrypoint."""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from cleanup import cleanup_stale_uploads
from config import Settings, reload_settings
from daemon import (
    clear_pid,
    clear_stop_flag,
    print_status,
    read_pid,
    resolve_bot_command,
    start_background,
    stop_background,
    stop_requested,
    tail_logs,
    write_pid,
)
from env_store import env_exists
from handlers import build_router
from middleware import AuthWhitelistMiddleware
from paths import package_dir
from platform_util import terminate_process
from projects import find_project_for_path, register_project
from session import AgentMode, SessionStore
from setup_cli import (
    apply_config_args,
    build_arg_parser,
    run_install,
    run_interactive_setup,
    show_config,
)

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def _temp_cleanup_loop(temp_dir: Path) -> None:
    while True:
        try:
            cleanup_stale_uploads(temp_dir, max_age_hours=4.0)
        except Exception:
            logger.debug("Temp cleanup failed", exc_info=True)
        await asyncio.sleep(30 * 60)


async def run_bot(settings: Settings) -> None:
    configure_logging(settings.log_level)

    default_ws = settings.default_workspace_path
    assert default_ws is not None
    try:
        project = register_project(default_ws)
        default_project_id = project.id
    except ValueError:
        existing = find_project_for_path(default_ws)
        default_project_id = existing.id if existing else None

    logger.info(
        "Starting telecursor default_ws=%s project=%s agent=%s mode=%s model=%s effort=%s",
        default_ws,
        default_project_id or "-",
        settings.agent_bin,
        settings.default_mode,
        settings.model_label,
        settings.effort_label,
    )

    sessions = SessionStore(
        default_mode=AgentMode(settings.default_mode),
        default_workspace=default_ws,
        default_model=settings.agent_model,
        default_effort=settings.agent_effort,
        default_project_id=default_project_id,
    )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AuthWhitelistMiddleware(settings))
    dp.callback_query.middleware(AuthWhitelistMiddleware(settings))
    dp.include_router(build_router(settings, sessions))

    Path(settings.temp_upload_dir).mkdir(parents=True, exist_ok=True)
    cleanup_stale_uploads(Path(settings.temp_upload_dir), max_age_hours=4.0)
    cleanup_task = asyncio.create_task(
        _temp_cleanup_loop(Path(settings.temp_upload_dir)),
        name="temp-cleanup",
    )

    me = await bot.get_me()
    logger.info("Bot online as @%s — send /start in Telegram", me.username)

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        await bot.session.close()
        logger.info("Bot stopped cleanly")


def _load_settings_or_exit() -> Settings:
    if not env_exists():
        print(
            "No .env found. Run setup first:\n"
            "  telecursor setup\n"
            "Or: python main.py setup"
        )
        sys.exit(1)
    try:
        return reload_settings()
    except Exception as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.error("Configuration error: %s", exc)
        print(
            "\nFix the config with:\n"
            "  telecursor setup\n"
            "  telecursor config --help\n"
            "  telecursor show"
        )
        sys.exit(1)


def _apply_start_cwd_workspace() -> Path:
    """Use the directory where `telecursor start` was invoked as the project path."""
    raw = os.environ.get("TELECURSOR_START_WORKSPACE")
    if raw:
        ws = Path(raw).expanduser().resolve()
    else:
        ws = Path.cwd().resolve()
    if not ws.is_dir():
        print(f"❌ Path is not a directory: {ws}")
        sys.exit(1)
    os.environ["TELECURSOR_START_WORKSPACE"] = str(ws)
    os.environ["ALLOWED_WORKSPACE_PATH"] = str(ws)
    os.environ["DEFAULT_WORKSPACE_PATH"] = str(ws)
    return ws


def _run_foreground(settings: Settings) -> None:
    """Run the bot in the current process (blocking)."""
    if os.environ.get("TELECURSOR_BACKGROUND") == "1" and os.environ.get(
        "TELECURSOR_SUPERVISE"
    ) != "1":
        write_pid(os.getpid())

        def _cleanup() -> None:
            if read_pid() == os.getpid():
                clear_pid()

        atexit.register(_cleanup)

    try:
        asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        logger.info("Interrupted")


def _run_supervised(workspace: Path) -> None:
    """
    Keep the bot alive across crashes. The PID file points at this supervisor.
    `telecursor stop` writes a stop flag and terminates this process.
    """
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    clear_stop_flag()
    write_pid(os.getpid())

    child: subprocess.Popen[bytes] | None = None

    def _cleanup() -> None:
        if child is not None and child.poll() is None:
            try:
                terminate_process(child.pid, force=False)
            except Exception:
                pass
        if read_pid() == os.getpid():
            clear_pid()

    atexit.register(_cleanup)

    restart = 0
    while not stop_requested():
        env = {
            **os.environ,
            "TELECURSOR_BACKGROUND": "1",
            "TELECURSOR_START_WORKSPACE": str(workspace),
            "ALLOWED_WORKSPACE_PATH": str(workspace),
            "DEFAULT_WORKSPACE_PATH": str(workspace),
        }
        env.pop("TELECURSOR_SUPERVISE", None)

        cmd = [*resolve_bot_command(), "start", "--foreground"]
        logger.info("Supervisor launching bot: %s", " ".join(cmd))
        child = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(package_dir()),
            env=env,
        )
        code = child.wait()
        child = None
        if stop_requested():
            break
        restart += 1
        logger.error(
            "Bot exited (code %s). Crash recovery restart #%s in 2s…",
            code,
            restart,
        )
        time.sleep(2.0)

    clear_stop_flag()
    if read_pid() == os.getpid():
        clear_pid()
    logger.info("Supervisor stopped")


def _maybe_background_after_setup(prefer_background: bool | None = None) -> None:
    """Ask (or honor flag) whether to start in background after setup."""
    workspace = _apply_start_cwd_workspace()
    print(f"Project (cwd): {workspace}")
    _load_settings_or_exit()
    if prefer_background:
        sys.exit(start_background(workspace=workspace))

    if prefer_background is False:
        print("\nStarting bot in foreground… (Ctrl+C to stop)\n")
        settings = _load_settings_or_exit()
        _run_foreground(settings)
        return

    if sys.stdin.isatty():
        choice = input(
            "\nStart bot in background so you can keep using this terminal? [Y/n]: "
        ).strip().lower()
        if choice in {"", "y", "yes"}:
            sys.exit(start_background(workspace=workspace))
        print("\nStarting bot in foreground… (Ctrl+C to stop)\n")
        settings = _load_settings_or_exit()
        _run_foreground(settings)
        return

    sys.exit(start_background(workspace=workspace))


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    command = args.command or "start"

    if command == "install":
        sys.exit(run_install(user=not getattr(args, "system", False)))

    if command == "setup":
        run_interactive_setup()
        _maybe_background_after_setup(
            True if getattr(args, "background", False) else None
        )
        return

    if command == "show":
        show_config()
        return

    if command == "config":
        apply_config_args(args)
        return

    if command == "status":
        sys.exit(print_status())

    if command == "stop":
        sys.exit(stop_background())

    if command == "logs":
        sys.exit(
            tail_logs(
                follow=bool(getattr(args, "follow", False)),
                lines=int(getattr(args, "lines", 50)),
            )
        )

    if command == "projects":
        from projects import list_projects, unregister_project

        action = getattr(args, "projects_action", None) or "list"
        if action == "remove":
            pid = getattr(args, "project_id", None)
            if not pid:
                print("Usage: telecursor projects remove <id>")
                sys.exit(2)
            if unregister_project(pid):
                print(f"✅ Removed project `{pid}`")
                sys.exit(0)
            print(f"❌ Unknown project `{pid}`")
            sys.exit(1)
        projects = list_projects()
        if not projects:
            print("No projects registered yet.")
            print("Run:  cd /path/to/project && telecursor start -d")
            sys.exit(0)
        print("Registered projects:")
        for proj in projects:
            print(f"  • {proj.id:20}  {proj.name:20}  {proj.path}")
        return

    if command == "start":
        workspace = _apply_start_cwd_workspace()
        print(f"Project (cwd): {workspace}")
        settings = _load_settings_or_exit()
        if getattr(args, "background", False) and not getattr(args, "foreground", False):
            sys.exit(start_background(workspace=workspace))
        if getattr(args, "supervise", False) or os.environ.get("TELECURSOR_SUPERVISE") == "1":
            os.environ["TELECURSOR_SUPERVISE"] = "1"
            _run_supervised(workspace)
            return
        _run_foreground(settings)
        return

    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
