#!/usr/bin/env python3
"""Telegram ↔ cursor-agent bridge — CLI entrypoint."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from config import Settings, reload_settings
from daemon import (
    clear_pid,
    print_status,
    read_pid,
    start_background,
    stop_background,
    tail_logs,
    write_pid,
)
from env_store import env_exists
from handlers import build_router
from middleware import AuthWhitelistMiddleware
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


async def run_bot(settings: Settings) -> None:
    configure_logging(settings.log_level)
    logger.info(
        "Starting cursor-bot jail=%s default_ws=%s agent=%s mode=%s model=%s",
        settings.allowed_workspace_path,
        settings.default_workspace_path,
        settings.agent_bin,
        settings.default_mode,
        settings.model_label,
    )

    sessions = SessionStore(
        default_mode=AgentMode(settings.default_mode),
        default_workspace=settings.default_workspace_path,  # type: ignore[arg-type]
        default_model=settings.agent_model,
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

    me = await bot.get_me()
    logger.info("Bot online as @%s — send /start in Telegram", me.username)

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
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


def _run_foreground(settings: Settings) -> None:
    """Run the bot in the current process (blocking)."""
    # If launched by the background helper, keep PID file accurate and clean up on exit
    if os.environ.get("TELECURSOR_BACKGROUND") == "1":
        write_pid(os.getpid())

        def _cleanup() -> None:
            if read_pid() == os.getpid():
                clear_pid()

        atexit.register(_cleanup)

    try:
        asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        logger.info("Interrupted")


def _maybe_background_after_setup(prefer_background: bool | None = None) -> None:
    """Ask (or honor flag) whether to start in background after setup."""
    settings = _load_settings_or_exit()
    if prefer_background:
        sys.exit(start_background())

    if prefer_background is False:
        print("\nStarting bot in foreground… (Ctrl+C to stop)\n")
        _run_foreground(settings)
        return

    # Interactive choice when setup was run without -d
    if sys.stdin.isatty():
        choice = input("\nStart bot in background so you can keep using this terminal? [Y/n]: ").strip().lower()
        if choice in {"", "y", "yes"}:
            sys.exit(start_background())
        print("\nStarting bot in foreground… (Ctrl+C to stop)\n")
        _run_foreground(settings)
        return

    # Non-interactive: default to background
    sys.exit(start_background())


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

    if command == "start":
        settings = _load_settings_or_exit()
        if getattr(args, "background", False) and not getattr(args, "foreground", False):
            sys.exit(start_background())
        _run_foreground(settings)
        return

    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
