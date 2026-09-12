"""Interactive setup and CLI config commands."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from cursor_info import resolve_agent_bin
from env_store import ENV_PATH, env_exists, read_env_map, redact_token, write_env_map


def _prompt(label: str, default: str | None = None, *, secret: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    while True:
        if secret:
            raw = getpass.getpass(f"{label}{hint}: ").strip()
        else:
            raw = input(f"{label}{hint}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        print("  Value required.")


def _prompt_path(label: str, default: str | None = None, *, must_exist: bool = True) -> str:
    while True:
        raw = _prompt(label, default)
        path = Path(raw).expanduser()
        if must_exist and (not path.exists() or not path.is_dir()):
            print(f"  Directory does not exist: {path}")
            create = input("  Create it? [y/N]: ").strip().lower()
            if create in {"y", "yes"}:
                path.mkdir(parents=True, exist_ok=True)
                return str(path.resolve())
            continue
        return str(path.expanduser().resolve())


def run_interactive_setup() -> dict[str, str]:
    print("\n=== Telecursor setup ===")
    print(f"Config file: {ENV_PATH}\n")
    existing = read_env_map()

    bot_token = _prompt(
        "Telegram bot token (from @BotFather)",
        existing.get("BOT_TOKEN"),
        secret=True,
    )
    allowed_users = _prompt(
        "Allowed users (comma-separated IDs and/or @usernames)",
        existing.get("ALLOWED_USERS"),
    )
    allowed_root = _prompt_path(
        "Allowed workspace jail (root path agent may use)",
        existing.get("ALLOWED_WORKSPACE_PATH") or str(Path.cwd()),
    )
    default_ws = _prompt_path(
        "Default workspace (must be inside jail)",
        existing.get("DEFAULT_WORKSPACE_PATH") or allowed_root,
    )
    detected = resolve_agent_bin(existing.get("AGENT_BIN"))
    agent_default = str(detected) if detected else existing.get("AGENT_BIN") or "agent"
    agent_bin = _prompt("Path to agent / cursor-agent binary", agent_default)
    mode = _prompt("Default mode (safe/yolo)", existing.get("DEFAULT_MODE") or "safe").lower()
    if mode not in {"safe", "yolo"}:
        mode = "safe"
    model = _prompt(
        "Default model (e.g. auto, composer-2.5; blank = auto)",
        existing.get("AGENT_MODEL") or "auto",
    )
    api_key = _prompt(
        "Cursor API key (optional — blank uses `agent login` session)",
        existing.get("CURSOR_API_KEY") or "",
        secret=True,
    )

    values = {
        "BOT_TOKEN": bot_token,
        "ALLOWED_USERS": allowed_users,
        "ALLOWED_WORKSPACE_PATH": allowed_root,
        "DEFAULT_WORKSPACE_PATH": default_ws,
        "AGENT_BIN": agent_bin,
        "DEFAULT_MODE": mode,
        "AGENT_MODEL": "" if model.lower() in {"", "auto", "default"} else model,
        "CURSOR_API_KEY": api_key,
        "TEMP_UPLOAD_DIR": existing.get("TEMP_UPLOAD_DIR") or "./temp_uploads",
        "STREAM_EDIT_INTERVAL": existing.get("STREAM_EDIT_INTERVAL") or "1.5",
        "MAX_CONCURRENT_RUNS_PER_USER": existing.get("MAX_CONCURRENT_RUNS_PER_USER") or "1",
        "LOG_LEVEL": existing.get("LOG_LEVEL") or "INFO",
    }
    write_env_map(values)
    print(f"\n✅ Saved {ENV_PATH}")
    return values


def apply_config_args(args: argparse.Namespace) -> dict[str, str]:
    updates: dict[str, str] = {}
    mapping = {
        "bot_token": "BOT_TOKEN",
        "allowed_users": "ALLOWED_USERS",
        "workspace": "ALLOWED_WORKSPACE_PATH",
        "default_workspace": "DEFAULT_WORKSPACE_PATH",
        "agent_bin": "AGENT_BIN",
        "api_key": "CURSOR_API_KEY",
        "mode": "DEFAULT_MODE",
        "model": "AGENT_MODEL",
        "log_level": "LOG_LEVEL",
        "temp_dir": "TEMP_UPLOAD_DIR",
    }
    for attr, env_key in mapping.items():
        value = getattr(args, attr, None)
        if value is not None:
            if env_key == "AGENT_MODEL" and str(value).lower() in {"auto", "default"}:
                updates[env_key] = ""
            else:
                updates[env_key] = str(value)

    # Convenience: if only --workspace set, also set default workspace
    if "ALLOWED_WORKSPACE_PATH" in updates and "DEFAULT_WORKSPACE_PATH" not in updates:
        updates["DEFAULT_WORKSPACE_PATH"] = updates["ALLOWED_WORKSPACE_PATH"]

    if not updates:
        print("No changes specified. See: python main.py config --help")
        return read_env_map()

    if not env_exists() and "BOT_TOKEN" not in updates:
        print("No .env yet. Run: python main.py setup")
        sys.exit(1)

    write_env_map(updates)
    print(f"✅ Updated {ENV_PATH}: {', '.join(updates.keys())}")
    return read_env_map()


def show_config() -> None:
    if not env_exists():
        print(f"No config at {ENV_PATH}. Run: python main.py setup")
        sys.exit(1)
    data = read_env_map()
    print(f"Config: {ENV_PATH}\n")
    for key in (
        "BOT_TOKEN",
        "ALLOWED_USERS",
        "ALLOWED_WORKSPACE_PATH",
        "DEFAULT_WORKSPACE_PATH",
        "AGENT_BIN",
        "DEFAULT_MODE",
        "AGENT_MODEL",
        "CURSOR_API_KEY",
        "TEMP_UPLOAD_DIR",
        "LOG_LEVEL",
    ):
        val = data.get(key, "")
        if key in {"BOT_TOKEN", "CURSOR_API_KEY"}:
            val = redact_token(val) if val else "(empty)"
        print(f"  {key}={val}")

    agent = resolve_agent_bin(data.get("AGENT_BIN"))
    print(f"\n  agent binary resolved: {agent or 'NOT FOUND'}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Telegram bridge for the local cursor-agent CLI",
    )
    sub = parser.add_subparsers(dest="command")

    setup_p = sub.add_parser("setup", help="Interactive setup wizard, then start the bot")
    setup_p.add_argument(
        "-d",
        "--background",
        "--daemon",
        dest="background",
        action="store_true",
        help="After setup, start the bot in the background",
    )

    start_p = sub.add_parser("start", help="Start the Telegram bot (default)")
    start_p.add_argument(
        "-d",
        "--background",
        "--daemon",
        dest="background",
        action="store_true",
        help="Run in the background and return to the shell",
    )
    start_p.add_argument(
        "--foreground",
        action="store_true",
        help=argparse.SUPPRESS,  # used by background launcher
    )

    sub.add_parser("stop", help="Stop the background bot process")
    sub.add_parser("status", help="Check whether the background bot is running")

    logs_p = sub.add_parser("logs", help="Show background bot logs")
    logs_p.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output (like tail -f)",
    )
    logs_p.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of recent lines to show (default: 50)",
    )

    sub.add_parser("show", help="Show current .env configuration (secrets redacted)")

    cfg = sub.add_parser("config", help="Update setup values from the command line")
    cfg.add_argument("--bot-token", dest="bot_token", help="Telegram bot token")
    cfg.add_argument(
        "--allowed-users",
        dest="allowed_users",
        help="Comma-separated Telegram user IDs and/or @usernames",
    )
    cfg.add_argument(
        "--workspace",
        dest="workspace",
        help="Allowed workspace jail root",
    )
    cfg.add_argument(
        "--default-workspace",
        dest="default_workspace",
        help="Default workspace (inside jail)",
    )
    cfg.add_argument("--agent-bin", dest="agent_bin", help="Path to agent binary")
    cfg.add_argument("--api-key", dest="api_key", help="Cursor API key")
    cfg.add_argument("--mode", dest="mode", choices=["safe", "yolo"], help="Default mode")
    cfg.add_argument("--model", dest="model", help="Default model id (or 'auto')")
    cfg.add_argument("--log-level", dest="log_level", help="DEBUG/INFO/WARNING/ERROR")
    cfg.add_argument("--temp-dir", dest="temp_dir", help="Temp upload directory")

    return parser
