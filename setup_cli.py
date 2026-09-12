"""Interactive setup, install, and CLI config commands."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cursor_info import resolve_agent_bin
from env_store import env_exists, get_env_path, read_env_map, redact_token, write_env_map
from paths import app_home, default_temp_upload_dir, package_dir


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
    print(f"Config file: {get_env_path()}\n")
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
        "TEMP_UPLOAD_DIR": existing.get("TEMP_UPLOAD_DIR")
        or str(default_temp_upload_dir()),
        "STREAM_EDIT_INTERVAL": existing.get("STREAM_EDIT_INTERVAL") or "1.5",
        "MAX_CONCURRENT_RUNS_PER_USER": existing.get("MAX_CONCURRENT_RUNS_PER_USER") or "1",
        "LOG_LEVEL": existing.get("LOG_LEVEL") or "INFO",
    }
    write_env_map(values)
    print(f"\n✅ Saved {get_env_path()}")
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

    if "ALLOWED_WORKSPACE_PATH" in updates and "DEFAULT_WORKSPACE_PATH" not in updates:
        updates["DEFAULT_WORKSPACE_PATH"] = updates["ALLOWED_WORKSPACE_PATH"]

    if not updates:
        print("No changes specified. See: telecursor config --help")
        return read_env_map()

    if not env_exists() and "BOT_TOKEN" not in updates:
        print("No .env yet. Run: telecursor setup")
        sys.exit(1)

    write_env_map(updates)
    print(f"✅ Updated {get_env_path()}: {', '.join(updates.keys())}")
    return read_env_map()


def show_config() -> None:
    if not env_exists():
        print(f"No config at {get_env_path()}. Run: telecursor setup")
        sys.exit(1)
    data = read_env_map()
    print(f"Home:   {app_home()}")
    print(f"Config: {get_env_path()}\n")
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
    which = _telecursor_bin()
    on_path = bool(shutil.which("telecursor"))
    if which and on_path:
        print(f"  telecursor command: {which}")
    elif which:
        print(f"  telecursor command: {which} (not on PATH)")
    else:
        print("  telecursor command: not found (run: telecursor install)")


def _scripts_dir() -> Path:
    """Return the bin/Scripts directory for the active interpreter (venv-safe)."""
    from platform_util import IS_WINDOWS

    # Do NOT resolve() the interpreter — on Linux venvs, python -> /usr/bin/python3
    # and resolving would point us at the system bin instead of the venv.
    literal = Path(sys.executable).parent
    for name in console_script_names_safe():
        if (literal / name).is_file():
            return literal

    prefix = Path(sys.prefix)
    if IS_WINDOWS:
        return prefix / "Scripts"
    return prefix / "bin"


def console_script_names_safe() -> list[str]:
    from platform_util import console_script_names

    return console_script_names()


def _realpath_if_file(path: Path) -> Path | None:
    """Return the resolved file path, or None if missing/broken."""
    try:
        if not path.exists() and not path.is_symlink():
            return None
        real = Path(os.path.realpath(path))
        if real.is_file():
            return real
    except OSError:
        return None
    return None


def _telecursor_bin() -> Path | None:
    """Return the real telecursor console script (never a ~/.local/bin shim)."""
    from platform_util import IS_WINDOWS

    # Prefer the active interpreter's scripts dir — not PATH shims.
    scripts = _scripts_dir()
    for name in console_script_names_safe():
        sibling = _realpath_if_file(scripts / name)
        if sibling is not None:
            return sibling

    which = shutil.which("telecursor")
    if which:
        candidate = Path(which)
        local_shim = Path.home() / ".local" / "bin" / "telecursor"
        # Ignore our own PATH shim; we need the underlying console script.
        try:
            same_shim = candidate.resolve() == local_shim.resolve()
        except OSError:
            same_shim = str(candidate) == str(local_shim)
        if not same_shim:
            real = _realpath_if_file(candidate)
            if real is not None:
                return real

    # Common global locations (skip ~/.local/bin shim itself)
    extras: list[Path] = []
    if IS_WINDOWS:
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        for pattern in ("Python/Python*/Scripts", "Python/Scripts"):
            extras.extend(roaming.glob(pattern))
    else:
        extras.append(Path.home() / ".local" / "bin")

    local_bin = Path.home() / ".local" / "bin"
    for folder in extras:
        for name in console_script_names_safe():
            candidate = folder / name
            if folder == local_bin and name == "telecursor":
                continue
            real = _realpath_if_file(candidate)
            if real is not None:
                return real
    return None


def _ensure_user_path_link(bin_path: Path) -> Path | None:
    """
    Install ~/.local/bin/telecursor as a small wrapper (Unix) so the command
    works without activating the venv. Avoids fragile self-referential symlinks.
    """
    from platform_util import IS_WINDOWS

    if IS_WINDOWS:
        return None

    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    target = local_bin / "telecursor"

    # Resolve the real binary BEFORE touching ~/.local/bin (which may be bin_path).
    real = _realpath_if_file(bin_path)
    if real is None or real == target:
        scripts_candidate = _scripts_dir() / "telecursor"
        real = _realpath_if_file(scripts_candidate)
    if real is None or not real.is_file():
        return None

    wrapper = f"#!/usr/bin/env bash\nexec \"{real}\" \"$@\"\n"
    try:
        if target.exists() or target.is_symlink():
            target.unlink()
        target.write_text(wrapper, encoding="utf-8")
        target.chmod(0o755)
    except OSError:
        return None
    return target


def _ensure_path_export(scripts_dir: Path) -> None:
    """Non-interactive PATH persistence for one-line installs."""
    from platform_util import IS_WINDOWS

    if IS_WINDOWS:
        try:
            ps = (
                f'$dir = "{scripts_dir}"; '
                f'$p = [Environment]::GetEnvironmentVariable("Path","User"); '
                f'if ($p -notlike ("*" + $dir + "*")) {{ '
                f'[Environment]::SetEnvironmentVariable("Path", $p + ";" + $dir, "User"); '
                f'Write-Output "updated" }} else {{ Write-Output "exists" }}'
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return

    # Prefer ~/.local/bin on PATH (where we place the symlink)
    path_dir = str(Path.home() / ".local" / "bin")
    profiles = [Path.home() / ".bashrc", Path.home() / ".profile"]
    zsh = Path.home() / ".zshrc"
    if zsh.is_file() or os.environ.get("SHELL", "").endswith("zsh"):
        profiles.insert(0, zsh)

    line = f'\n# Telecursor CLI\nexport PATH="{path_dir}:$PATH"\n'
    for profile in profiles:
        try:
            existing = profile.read_text(encoding="utf-8") if profile.is_file() else ""
            if path_dir in existing and "Telecursor CLI" in existing:
                continue
            if path_dir in existing:
                continue
            with profile.open("a", encoding="utf-8") as fh:
                fh.write(line)
            print(f"✅ Added PATH entry to {profile}")
            break
        except OSError:
            continue


def run_install(*, user: bool = True) -> int:
    """
    Install this project so the `telecursor` command is available globally.

    Uses an editable install from the source checkout (`pip install -e .`).
    """
    from platform_util import IS_WINDOWS

    root = package_dir()
    if not (root / "pyproject.toml").is_file():
        print("❌ Cannot locate pyproject.toml next to the package.")
        print("   Clone the repo and run install from that directory.")
        return 1

    in_venv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix
    # Never use --user inside a virtualenv
    use_user = bool(user) and not in_venv and not IS_WINDOWS

    cmd = [sys.executable, "-m", "pip", "install", "-e", str(root)]
    if use_user:
        cmd.insert(4, "--user")

    print(f"Installing telecursor from {root} …")
    print(f"$ {' '.join(cmd)}")
    try:
        subprocess.check_call(cmd)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ pip install failed (exit {exc.returncode})")
        if use_user:
            print("Retrying without --user …")
            return run_install(user=False)
        return exc.returncode or 1

    found = _telecursor_bin()
    if found is None:
        # Last resort: package dir venv
        for name in console_script_names_safe():
            candidate = root / ".venv" / "bin" / name
            if not candidate.is_file():
                candidate = root / ".venv" / "Scripts" / name
            if candidate.is_file():
                found = candidate
                break

    if found is None:
        print("\n⚠️ Package installed but the console script was not found.")
        print("   Try:  python -m pip show -f telecursor")
        return 1

    link = _ensure_user_path_link(found)
    _ensure_path_export(found.parent)

    on_path = bool(shutil.which("telecursor"))
    print("\n✅ Installed. You can now run:  telecursor")
    print(f"   Binary: {found}")
    if link:
        print(f"   Link:   {link}")
    if not on_path and link:
        print("   Open a new terminal (or: source ~/.bashrc) if `telecursor` is not found yet.")
    print("\nNext steps:")
    print("  telecursor setup")
    print("  telecursor start -d")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telecursor",
        description="Telegram bridge for the local Cursor Agent CLI",
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
        help=argparse.SUPPRESS,
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

    install_p = sub.add_parser(
        "install",
        help="Install the `telecursor` command on PATH (pip install -e .)",
    )
    install_p.add_argument(
        "--system",
        action="store_true",
        help="Install into the active environment without pip --user",
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
